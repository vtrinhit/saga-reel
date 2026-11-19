"""Đối chiếu op – REST – CLI: mọi op trong REGISTRY phải có route REST và lệnh CLI; cờ annotation hợp lệ.

Duyệt `REGISTRY` sống nên op thêm sau này tự được kiểm. Khung MCP thêm vào đây ở M2.
"""

from __future__ import annotations

from sagareel.cli.main import build_parser, command_for
from sagareel.core import ops


def annotation_problems(catalog: list[dict]) -> list[str]:
    """Lỗi cờ annotation: op `destructive` phải có op xem trước; cờ không được mâu thuẫn."""
    names = {o["name"] for o in catalog}
    problems = []
    for o in catalog:
        if o["destructive"]:
            previews = (f"{o['group']}.delete_preview", f"{o['name']}_preview")
            if not any(p in names for p in previews):
                problems.append(f"{o['name']}: destructive nhưng thiếu op {previews[0]} hoặc {previews[1]}")
            if o["read_only"]:
                problems.append(f"{o['name']}: vừa read_only vừa destructive")
    return problems


def test_every_op_has_rest_and_cli(client):
    catalog = client.get("/api/ops").json()
    openapi = client.get("/openapi.json").json()
    parser = build_parser(catalog)

    assert ops.REGISTRY, "REGISTRY rỗng — ops chưa được nạp"
    assert {o["name"] for o in catalog} == set(ops.REGISTRY)
    for name, spec in ops.REGISTRY.items():
        path = openapi["paths"].get(f"/api/ops/{name}", {})
        assert "post" in path, f"{name}: thiếu route REST POST /api/ops/{name}"
        assert path["post"]["operationId"] == name

        group, action = command_for(name)
        assert group == spec.group
        args = parser.parse_args([group, action])
        assert args.op == name, f"{name}: lệnh CLI `sagareel {group} {action}` không trỏ về op này"

    assert annotation_problems(catalog) == []


def _entry(name: str, *, destructive: bool = False, read_only: bool = False) -> dict:
    return {"name": name, "group": name.split(".", 1)[0], "destructive": destructive, "read_only": read_only}


def test_annotation_check_catches_destructive_without_preview():
    assert annotation_problems([_entry("provider.delete", destructive=True)]) == [
        "provider.delete: destructive nhưng thiếu op provider.delete_preview hoặc provider.delete_preview"
    ]
    assert annotation_problems([_entry("provider.delete", destructive=True), _entry("provider.delete_preview")]) == []
    assert annotation_problems([_entry("clip.purge", destructive=True), _entry("clip.purge_preview")]) == []
    assert annotation_problems([_entry("x.wipe", destructive=True, read_only=True), _entry("x.wipe_preview")]) == [
        "x.wipe: vừa read_only vừa destructive"
    ]
