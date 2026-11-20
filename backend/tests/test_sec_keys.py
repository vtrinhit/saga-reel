"""Rà soát bảo mật — key project/tập trùng khi bỏ qua hoa thường (#9): trên Windows `Demo` và `demo` cùng thư mục xuất."""

from __future__ import annotations

import pytest
from sagareel.core.errors import AppError
from sagareel.services import projects, sequences
from sagareel.services.export import package


def test_project_key_case_insensitive_unique(owner_ctx, project):
    with pytest.raises(AppError) as e:
        projects.create(owner_ctx, name="Khác", key="DEMO")
    assert e.value.code == "key_taken"


def test_project_auto_key_avoids_case_variant(owner_ctx, project):
    out = projects.create(owner_ctx, name="demo")  # slug "demo" đã có → tự sinh khác
    assert out["key"].lower() != "demo"


def test_sequence_key_case_insensitive_unique(owner_ctx, project):
    sequences.create(owner_ctx, project, "Tập 1", key="ep1")
    with pytest.raises(AppError) as e:
        sequences.create(owner_ctx, project, "Tập 1b", key="EP1")
    assert e.value.code == "key_taken"


def test_export_folder_lowercase_with_project_id(services):
    a = package.package_folder(services, "Demo", "EP1", "tiktok", "vi", project_id=7)
    b = package.package_folder(services, "demo", "ep1", "tiktok", "vi", project_id=8)
    assert a.parent.parent.name == "demo-7" and a.parent.name == "ep1"
    assert a != b


# --- #10: export.check không cho người xem, loudness đo một lần mỗi bản dựng -----------------------------------------


def test_export_check_not_for_viewer():
    from sagareel import ops as ops_pkg
    from sagareel.core import ops

    ops_pkg.load_all()
    assert ops.REGISTRY["export.check"].viewer is False


async def test_export_check_caches_loudness_per_render_sha(services, owner_ctx, project, make_clip_tiny, monkeypatch):
    from sagareel.db.models import Render, Sequence

    calls = []

    async def fake_measure(path):
        calls.append(path)
        return {"integrated_lufs": -14.0, "true_peak_db": -1.5}

    monkeypatch.setattr(package, "measure_loudness", fake_measure)
    mid = make_clip_tiny(owner_ctx.services.db, project)
    with owner_ctx.uow(op_name="test.seed") as s:
        seq = Sequence(project_id=project, key="ep1", title="T")
        s.add(seq)
        s.flush()
        s.add(Render(sequence_id=seq.id, variant="final", platform="tiktok", lang="vi", status="done", media_id=mid,
                     duration_ms=1000))
        sid = seq.id
    a = await package.precheck(services, sid, "tiktok", "vi")
    b = await package.precheck(services, sid, "tiktok", "vi")
    assert len(calls) == 1 and a["warnings"] == b["warnings"]
