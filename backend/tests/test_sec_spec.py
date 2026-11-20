"""Rà soát bảo mật — file khai báo project: key thoát thư mục (#3), upsert khớp dòng đã xoá mềm (#6), extras của
`ClipAssetRef` ghi đè id (#7), YAML alias bomb (#8), mime/kind media lấy từ file thật (#2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sagareel.core.errors import AppError
from sagareel.db.models import Asset, Clip, Media, Project
from sagareel.ops import spec_ops
from sagareel.services import entities, library, safe_yaml
from sagareel.services.spec import plan as sp
from sqlalchemy import select, text


def _spec(project_key: str = "moi", extra: str = "") -> str:
    return f"""
spec_version: 1
project: {{key: "{project_key}", name: Dự án}}
{extra}"""


@pytest.mark.parametrize("bad", ["../../evil", "a/b", "..", "a\\\\b", "x y", ".hidden", "-x"])
def test_project_key_must_match_service_regex(owner_ctx, bad):
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, _spec(bad))
    assert e.value.code == "spec_invalid"


@pytest.mark.parametrize("path", [
    "library: {assets: [{key: '../x', kind: character, name: X}]}",
    "groups: [{key: 'a/b', kind: series, title: S}]",
    "sequences: [{key: '../ep', title: T}]",
    "sequences: [{key: ep1, title: T, clips: [{key: '../c'}]}]",
    "presets: [{kind: voice, key: 'A B', name: X}]",
])
def test_nested_keys_validated(owner_ctx, path):
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, _spec(extra=path))
    assert e.value.code == "spec_invalid"


def test_export_spec_refuses_path_escape(owner_ctx, project, settings):
    with owner_ctx.services.db.session() as s:
        s.execute(text("UPDATE project SET key = '../../escaped' WHERE id = :i"), {"i": project})
    with pytest.raises(AppError):
        spec_ops.export_spec(owner_ctx, project, None, True)
    assert not list(Path(settings.outputs_dir).parent.rglob("escaped*.yaml"))


def _asset(ctx, project) -> int:
    return library.asset_create(ctx, project, "character", "An", key="an")["id"]


def test_upsert_ignores_soft_deleted_rows(owner_ctx, project):
    """Có cả dòng đã xoá mềm và dòng sống cùng key → apply phải sửa dòng sống (trước đây hồi sinh dòng cũ → key_taken)."""
    old = _asset(owner_ctx, project)
    entities.soft_delete(owner_ctx, entities.soft_delete_preview(owner_ctx, "asset", [old])["confirm_token"])
    live = _asset(owner_ctx, project)
    text_ = _spec("demo", "library: {assets: [{key: an, kind: character, name: An mới}]}")
    p = sp.plan(owner_ctx, text_)
    assert [u["key"] for u in p["updates"] if u["type"] == "asset"] == ["an"]
    sp.apply(owner_ctx, text_, p["plan_fingerprint"])
    with owner_ctx.services.db.session() as s:
        assert s.get(Asset, live).name == "An mới"
        assert s.get(Asset, old).deleted_at is not None


def test_upsert_creates_when_only_deleted_row(owner_ctx, project):
    old = _asset(owner_ctx, project)
    entities.soft_delete(owner_ctx, entities.soft_delete_preview(owner_ctx, "asset", [old])["confirm_token"])
    text_ = _spec("demo", "library: {assets: [{key: an, kind: character, name: An lại}]}")
    p = sp.plan(owner_ctx, text_)
    assert {"type": "asset", "key": "an"} in p["creates"]
    sp.apply(owner_ctx, text_, p["plan_fingerprint"])
    with owner_ctx.services.db.session() as s:
        rows = s.scalars(select(Asset).where(Asset.key == "an", Asset.deleted_at.is_(None))).all()
    assert len(rows) == 1


def test_clip_asset_ref_extras_cannot_override_ids(owner_ctx):
    bad = _spec(extra="""library: {assets: [{key: a, kind: character, name: A}]}
sequences: [{key: ep1, title: T, clips: [{key: c1, assets: [{asset: a, asset_id: 999, variant_id: 5}]}]}]""")
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, bad)
    assert e.value.code == "spec_invalid"


def test_clip_asset_ref_known_extra_kept_but_ids_resolved(owner_ctx):
    ok = _spec(extra="""library: {assets: [{key: a, kind: character, name: A}]}
sequences: [{key: ep1, title: T, clips: [{key: c1, assets: [{asset: a, code: AN}]}]}]""")
    p = sp.plan(owner_ctx, ok)
    out = sp.apply(owner_ctx, ok, p["plan_fingerprint"])
    with owner_ctx.services.db.session() as s:
        aid = s.scalar(select(Asset.id).where(Asset.project_id == out["project_id"], Asset.key == "a"))
        clip = s.scalar(select(Clip).where(Clip.key == "c1"))
        assert clip.assets == [{"asset_id": aid, "code": "AN"}]


BOMB = """a: &a ["x","x","x","x","x","x","x","x","x"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]
e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]
f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]
g: [*f,*f,*f,*f,*f,*f,*f,*f,*f]
"""


def test_yaml_alias_bomb_rejected(owner_ctx):
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, "spec_version: 1\nproject: {key: x, name: X}\n" + BOMB)
    assert e.value.code == "spec_invalid"


def test_safe_yaml_caps_expanded_nodes():
    assert safe_yaml.load("a: &x [1, 2]\nb: *x\n") == {"a": [1, 2], "b": [1, 2]}  # alias nhỏ vẫn hợp lệ
    with pytest.raises(ValueError):
        safe_yaml.load(BOMB)
    with pytest.raises(ValueError):
        safe_yaml.load("a: &x [1, *x]\n")
    with pytest.raises(ValueError):
        safe_yaml.load("[" + ",".join(["1"] * 50) + "]", max_nodes=10)
    assert safe_yaml.load("a: [1, 2]\nb: {c: d}\n") == {"a": [1, 2], "b": {"c": "d"}}


def test_importer_yaml_rejects_alias_bomb(owner_ctx, project):
    from sagareel.services import importer

    with pytest.raises(AppError):
        importer.parse_structured(BOMB, "yaml", [])


def test_spec_media_kind_and_mime_from_file_not_spec(owner_ctx, settings, tmp_path):
    """Spec khai `kind: image, mime: image/png` cho một file HTML → apply không được lưu mime/kind theo spec."""
    import hashlib

    data_dir = Path(settings.data_dir)
    f = data_dir / "media" / "x" / "evil.png"
    f.parent.mkdir(parents=True, exist_ok=True)
    body = b"<html><script>alert(1)</script></html>"
    f.write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    rel = f.relative_to(data_dir).as_posix()
    spec_text = _spec(extra=f"""media: {{m1: {{sha256: {sha}, path: {rel}, kind: image, mime: text/html}}}}
library: {{assets: [{{key: a, kind: character, name: A, cover: m1}}]}}""")
    p = sp.plan(owner_ctx, spec_text)
    sp.apply(owner_ctx, spec_text, p["plan_fingerprint"])
    with owner_ctx.services.db.session() as s:
        rows = s.scalars(select(Media).where(Media.sha256 == sha)).all()
    for r in rows:
        assert r.mime not in ("text/html", "image/png") and r.kind != "image"


def test_project_lookup_case_insensitive_in_spec(owner_ctx, project):
    p = sp.plan(owner_ctx, _spec("DEMO"))
    assert p["project_id"] == project
    with owner_ctx.services.db.session() as s:
        assert len(s.scalars(select(Project)).all()) == 1
