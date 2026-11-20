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
