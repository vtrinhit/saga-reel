"""Prompt là dữ liệu của project; mẫu = dự án mẫu nhân bản được (không còn template chung của tool)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text

from sagareel.compile import compiler
from sagareel.compile import prompt_contracts as C
from sagareel.core import ops as core_ops
from sagareel.core import templating
from sagareel.core.errors import AppError
from sagareel.db.models import Clip, Project, ProjectPrompt, Sequence
from sagareel.db.session import Database
from sagareel.services import projects, prompts, samples
from test_m1_schema import _upgrade


def rest(client, op: str, body: dict, status: int = 200) -> dict:
    r = client.post(f"/api/ops/{op}", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _clip(owner_ctx, project_id: int, prompt: str = "a boy runs") -> int:
    with owner_ctx.services.db.session() as s:
        q = Sequence(project_id=project_id, key=f"e{project_id}", number=1, title="Tập 1")
        s.add(q)
        s.flush()
        c = Clip(sequence_id=q.id, key="c1", rank="a", duration_ms=6000, image_prompt=prompt, motion_prompt="walks")
        s.add(c)
        s.flush()
        return c.id


def _set(owner_ctx, project_id: int, key: str, body: str) -> dict:
    return prompts.set_prompt(owner_ctx, project_id, key, body)


# --- Hợp đồng -------------------------------------------------------------------------------------------------------


def test_contract_covers_every_llm_task_and_compile_key():
    from sagareel.services import llm

    task_keys = {t["template_key"] for t in llm.default_tasks()}
    assert task_keys <= set(C.KEYS)
    assert set(compiler.TEMPLATE_KINDS) <= set(C.KEYS)


@pytest.mark.parametrize("slug", ["phim-thoai-hoat-hinh", "ke-chuyen-xua", "phim-truyen-nguoi-that"])
def test_sample_prompts_complete_and_match_contract(slug):
    d = samples.definition(slug)
    assert set(d["prompts"]) == set(C.KEYS), slug
    for key, body in d["prompts"].items():
        used = templating.variables_of(body)
        assert not C.check_variables(key, used), (slug, key, used)


def test_contract_used_by_ops_exist():
    from sagareel import ops

    ops.load_all()
    names = set(core_ops.REGISTRY)
    for c in C.CONTRACTS:
        for name in c.used_by:
            if "(" not in name:
                assert name in names, (c.key, name)


def test_compile_variables_parity_with_contract(owner_ctx, project, monkeypatch):
    """Biến compiler truyền vào khi render == biến khai trong hợp đồng."""
    seen: dict[str, set[str]] = {}
    real = compiler._render

    def spy(body, variables):
        seen[current[0]] = set(variables)
        return real(body, variables)

    current = [""]
    monkeypatch.setattr(compiler, "_render", spy)
    cid = _clip(owner_ctx, project)
    with owner_ctx.services.db.session() as s:
        current[0] = "image.synced"
        compiler.compile_clip_image(s, cid)
        current[0] = "motion.synced"
        compiler.compile_clip_motion(s, cid)
    for key in ("image.synced", "motion.synced"):
        assert seen[key] == set(C.BY_KEY[key].variables), key


def test_contracts_op(client):
    out = rest(client, "prompt.contracts", {})
    keys = [x["key"] for x in out["items"]]
    assert "image.synced" in keys and "story_split_clips" in keys
    split = next(x for x in out["items"] if x["key"] == "story_split_clips")
    assert split["output_schema"]["title"] == "SplitOut" and split["json"] is True
    assert {v["name"] for v in split["variables"]} >= {"script", "cast", "genre"}


# --- Phân giải theo project -------------------------------------------------------------------------------------------


def test_two_projects_different_prompts_compile_differently(owner_ctx):
    a = projects.create(owner_ctx, name="A", key="a")["id"]
    b = projects.create(owner_ctx, name="B", key="b")["id"]
    _set(owner_ctx, a, "image.synced", "A-STYLE {{ prompt }}")
    _set(owner_ctx, b, "image.synced", "B-STYLE {{ prompt }} {{ aspect }}")
    ca, cb = _clip(owner_ctx, a, "mèo"), _clip(owner_ctx, b, "chó")
    with owner_ctx.services.db.session() as s:
        assert compiler.compile_clip_image(s, ca).text == "A-STYLE mèo"
        assert compiler.compile_clip_image(s, cb).text == "B-STYLE chó 9:16"


def test_missing_prompt_is_clear_error(owner_ctx):
    pid = projects.create(owner_ctx, name="Trống", key="trong")["id"]
    cid = _clip(owner_ctx, pid)
    with owner_ctx.services.db.session() as s, pytest.raises(AppError) as e:
        compiler.compile_clip_image(s, cid)
    assert e.value.code == "prompt_missing" and "prompt.set" in e.value.hint
    assert e.value.extra == {"key": "image.synced", "project_id": pid}


async def test_llm_story_task_uses_project_prompt(owner_ctx, monkeypatch):
    from sagareel.services import llm

    a = projects.create(owner_ctx, name="A", key="a")["id"]
    _set(owner_ctx, a, "story_write_script", "<!-- role: user -->\nRIÊNG-A {{ episode }}")
    with owner_ctx.services.db.session() as s:
        assert llm.template_body(s, "story_write_script", a).endswith("RIÊNG-A {{ episode }}")
        with pytest.raises(AppError) as e:
            llm.template_body(s, "story_write_script", None)
    assert e.value.code == "prompt_missing"


# --- Op prompt.* --------------------------------------------------------------------------------------------------


def test_prompt_ops_roundtrip(client):
    pid = rest(client, "project.create", {"name": "Thử prompt"})["id"]
    listed = rest(client, "prompt.list", {"project_id": pid})
    assert listed["items"] == [] and set(listed["missing"]) == set(C.KEYS)
    out = rest(client, "prompt.set", {"project_id": pid, "key": "caption", "body": "{{ input }} {{ la }}"})
    assert any("'la'" in w for w in out["warnings"])
    got = rest(client, "prompt.get", {"project_id": pid, "key": "caption"})
    assert got["body"] == "{{ input }} {{ la }}" and got["contract"]["key"] == "caption"
    rest(client, "prompt.set", {"project_id": pid, "key": "caption", "body": "{% if %}"}, 422)
    bad = rest(client, "prompt.set", {"project_id": pid, "key": "khong_co", "body": "x"}, 422)
    assert bad["error_code"] == "prompt_key_unknown"
    prev = rest(client, "prompt.preview", {"project_id": pid, "key": "caption", "variables": {"input": "XIN"}})
    assert prev["messages"][0]["content"].startswith("XIN <la>")
    pv = rest(client, "prompt.delete_preview", {"project_id": pid, "keys": ["caption"]})
    rest(client, "prompt.delete", {"confirm_token": pv["confirm_token"]})
    assert rest(client, "prompt.list", {"project_id": pid})["items"] == []


def test_prompt_preview_compile_with_real_clip(owner_ctx, project, client):
    _clip(owner_ctx, project, "cô bé hái hoa")
    out = rest(client, "prompt.preview", {"project_id": project, "key": "image.synced",
                                          "body": "THỬ {{ prompt }}"})
    assert out["text"] == "THỬ cô bé hái hoa" and out["kind"] == "compile"


def test_removed_ops_gone():
    from sagareel import ops

    ops.load_all()
    names = set(core_ops.REGISTRY)
    assert not {n for n in names if n.startswith("template.")}
    assert "library.templates" not in names
    assert {"prompt.contracts", "prompt.list", "prompt.get", "prompt.set", "prompt.preview", "prompt.delete",
            "prompt.delete_preview", "sample.reseed"} <= names


# --- Dự án mẫu + nhân bản ----------------------------------------------------------------------------------------


def test_seed_samples_idempotent_and_respects_deletion(owner_ctx):
    db = owner_ctx.services.db
    created = samples.seed(db)
    assert len(created) == 3
    assert samples.seed(db) == []
    with db.session() as s:
        rows = s.scalars(select(Project).where(Project.is_sample.is_(True))).all()
        assert len(rows) == 3
        dialog = next(p for p in rows if p.key == "mau-phim-thoai-hoat-hinh")
        assert dialog.genre["clip_s"] and "full_auto" in dialog.workflows
        body = prompts.body(s, "motion.synced", dialog.id)
        assert "first half second" in body and "d.voice" in body
        narr = next(p for p in rows if p.key == "mau-ke-chuyen-xua")
        assert narr.narrator_voice
        assert '"narrator"' in prompts.body(s, "story_split_clips", narr.id)
    pv = projects.delete_preview(owner_ctx, dialog.id)
    projects.delete(owner_ctx, pv["confirm_token"])
    assert samples.seed(db) == []  # mẫu đã xoá không tự quay lại
    out = samples.reseed(owner_ctx)
    assert [x["key"] for x in out["created"]] == ["mau-phim-thoai-hoat-hinh"]


def test_create_from_sample_copies_prompts_and_style(owner_ctx):
    samples.seed(owner_ctx.services.db)
    with owner_ctx.services.db.session() as s:
        src = samples.find_sample(s, "narration")
    p = projects.create(owner_ctx, name="Truyện mới", from_project_id=src.id)
    assert p["is_sample"] is False and p["style_block"] == src.style_block and p["genre_key"] == "narration"
    with owner_ctx.services.db.session() as s:
        assert prompts.bodies(s, p["id"]) == prompts.bodies(s, src.id)
    bare = projects.duplicate(owner_ctx, src.id, "Không prompt", include={"prompts": False, "style": False})
    assert bare["style_block"] == ""
    with owner_ctx.services.db.session() as s:
        assert prompts.bodies(s, bare["id"]) == {}
    with pytest.raises(AppError):
        projects.duplicate(owner_ctx, src.id, "X", include={"lạ": True})


def test_edit_prompt_affects_only_that_project(owner_ctx):
    samples.seed(owner_ctx.services.db)
    with owner_ctx.services.db.session() as s:
        src = samples.find_sample(s, "series")
    a = projects.create(owner_ctx, name="A", from_project_id=src.id)["id"]
    b = projects.create(owner_ctx, name="B", from_project_id=src.id)["id"]
    _set(owner_ctx, a, "caption", "CHỈ A {{ input }}")
    with owner_ctx.services.db.session() as s:
        assert prompts.body(s, "caption", a) == "CHỈ A {{ input }}"
        assert prompts.body(s, "caption", b) == prompts.body(s, "caption", src.id)


def test_blank_project_has_workflows_and_genre_but_no_prompts(owner_ctx):
    p = projects.create(owner_ctx, name="Trống", genre_key="narration")
    assert p["genre"]["clip_s"] and "full_auto" in p["workflows"]
    with owner_ctx.services.db.session() as s:
        assert prompts.bodies(s, p["id"]) == {}


def test_backfill_once(owner_ctx):
    db = owner_ctx.services.db
    with db.session() as s:
        for k, g in (("n", "narration"), ("s", "series"), ("x", "serial")):
            s.add(Project(key=k, name=k, genre_key=g))
    assert samples.backfill_once(db) == 3
    with db.session() as s:
        ids = {p.key: p.id for p in s.scalars(select(Project)).all()}
        assert "first half second" in prompts.body(s, "motion.synced", ids["s"])
        assert "no lip movement" in prompts.body(s, "motion.synced", ids["n"])
        s.add(Project(key="moi", name="Mới"))
    assert samples.backfill_once(db) == 0  # chỉ một lần: dự án trống về sau không bị tự điền
    with db.session() as s:
        assert s.scalar(select(ProjectPrompt.id).join(Project, Project.id == ProjectPrompt.project_id)
                        .where(Project.key == "moi")) is None


# --- Migration ------------------------------------------------------------------------------------------------------


def test_migration_moves_genre_workflow_and_drops_template(tmp_path):
    database = Database(tmp_path / "old.db")
    try:
        _upgrade(database, "0010")
        now = "2026-11-20 00:00:00"
        with database.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO project (key, name, description, genre_key, aspect, resolution, fps, language, languages, "
                "target_platforms, style_block, negative_block, settings, created_at, updated_at) VALUES ('p', 'P', '', "
                "'narration', '9:16', '1080p', 30, 'vi', '[\"vi\"]', '[]', '', '', :st, :t, :t)"),
                {"st": json.dumps({"narrator_voice_desc": "giọng ấm", "x": 1}), "t": now})
            for kind, key, data in (("genre", "narration", {"clip_s": [3, 4]}), ("genre", "series", {"clip_s": [6, 10]}),
                                    ("workflow", "full_auto", {"steps": [{"step": "write_bible"}]}),
                                    ("style_pack", "sp", {})):
                conn.execute(text(
                    "INSERT INTO preset (kind, key, name, data, builtin, builtin_hash, created_at, updated_at) "
                    "VALUES (:k, :key, :key, :d, 1, '', :t, :t)"), {"k": kind, "key": key, "d": json.dumps(data), "t": now})
            conn.execute(text("INSERT INTO llm_task (key, label, description, template_key, needs_vision, json_mode, "
                              "route, fallbacks, created_at, updated_at) VALUES ('outline', 'x', '', 'outline', 0, 1, "
                              "'{}', '[]', :t, :t)"), {"t": now})
        database.migrate()
        with database.engine.connect() as conn:
            genre, wf, voice, st = conn.execute(text(
                "SELECT genre, workflows, narrator_voice, settings FROM project WHERE key='p'")).one()
            assert json.loads(genre) == {"clip_s": [3, 4]}
            assert json.loads(wf)["full_auto"]["steps"] == [{"step": "write_bible"}]
            assert voice == "giọng ấm" and json.loads(st) == {"x": 1}
            assert conn.execute(text("SELECT count(*) FROM preset WHERE kind IN ('genre','workflow','style_pack')")
                                ).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM llm_task WHERE key='outline'")).scalar() == 0
            tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            assert "template" not in tables and "project_prompt" in tables
    finally:
        database.dispose()


# --- Hai chế độ: tool LLM / agent gửi thẳng -------------------------------------------------------------------------


def test_llm_run_appends_tool_output_format(owner_ctx, project):
    from sagareel.services import llm

    with owner_ctx.services.db.session() as s:
        from sagareel.db.models import LlmTask

        for t in llm.default_tasks():
            s.add(LlmTask(**t))
        s.add(__import__("sagareel.db.models", fromlist=["ProviderConnection"]).ProviderConnection(
            kind="llm", adapter="fake_llm", name="f", options={}, concurrency=1, enabled=True))
        s.flush()
        body = prompts.body(s, "story_split_clips", project)
        assert "Chỉ trả về MỘT giá trị JSON" not in body  # prompt dự án chỉ giữ phần sáng tạo
        plan = llm.plan(s, "story_split_clips", {k: "x" for k in ("genre", "rules", "cast", "enums", "episode", "script",
                                                                   "feedback", "fix_error")}, project_id=project)
    system = plan.messages[0]
    assert system["role"] == "system" and system["content"].endswith(C.output_format("story_split_clips"))
    assert '"scenes"' in system["content"]


async def test_story_submit_split_clips_and_script_without_llm(owner_ctx, client):
    from sagareel.services import sequences

    pid = rest(client, "project.create", {"name": "Agent tự viết"})["id"]  # dự án trống: không prompt, không LLM
    sid = sequences.create(owner_ctx, project_id=pid, title="T1")["id"]
    out = rest(client, "story.submit", {"task": "write_script", "sequence_id": sid,
                                        "data": "CẢNH 1 — Sân nhà — Sáng\nAN (vui): Mẹ ơi, con về rồi!"})
    assert out["task"] == "write_script"
    assert "Mẹ ơi" in rest(client, "sequence.get", {"id": sid})["script"]
    split = {"scenes": [{"key": "s01", "title": "Sân nhà"}],
             "clips": [{"scene": "s01", "duration_s": 6, "action": "An chạy vào sân", "shot_size": "ws",
                        "dialogue": [{"speaker": "narrator", "text": "Chiều ấy An về."}]}]}
    rest(client, "story.submit", {"task": "split_clips", "sequence_id": sid, "data": split})
    clips = rest(client, "clip.list", {"sequence_id": sid})["items"]
    assert len(clips) == 1
    bad = rest(client, "story.submit", {"task": "split_clips", "sequence_id": sid, "data": {"clips": "x"}}, 422)
    assert bad["error_code"] == "submit_invalid"


def test_prompt_check_and_guide(client):
    pid = rest(client, "project.create", {"name": "Trống"})["id"]
    chk = rest(client, "prompt.check", {"project_id": pid})
    assert chk["ok"] is False and "story_write_script" in chk["missing"]
    assert "write_script" in chk["steps_without_prompt"]
    rest(client, "prompt.set", {"project_id": pid, "key": "caption", "body": "{{ input }} {{ lạ }}"})
    chk = rest(client, "prompt.check", {"project_id": pid})
    assert any(w["key"] == "caption" and w["code"] == "prompt_variables" for w in chk["warnings"])
    g = rest(client, "studio.guide", {"project_id": pid})
    steps = {s["step"]: s for s in g["steps"]}
    assert "story.submit task=split_clips" in steps["split_clips"]["agent_op"]
    assert steps["split_clips"]["prompts"][0]["output_schema"]["title"] == "SplitOut"
    assert g["state"]["prompts"]["missing"] and g["state"]["project"]["id"] == pid
