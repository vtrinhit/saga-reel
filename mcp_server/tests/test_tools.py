from __future__ import annotations

from sagareel_mcp.tools import TOOL_NAME_RE, build_tools, mapping_problems, tool_name


def test_tool_name():
    assert tool_name("project.delete_preview") == "sr_project_delete_preview"


def test_every_tool_op_has_a_tool(catalog):
    tools = build_tools(catalog)
    for o in catalog:
        if o["mcp"] != "tool":
            continue
        if o["name"] == "job.wait":
            assert tools["sr_job_wait"].kind == "job_wait"
            continue
        t = tools[tool_name(o["name"])]
        assert t.op == o["name"]
        assert t.input_schema["type"] == "object"
        assert t.input_schema.get("properties") == o["params_schema"].get("properties", {})
    # Op "call" không có tool riêng (trừ bí danh), vẫn gọi được qua sr_call.
    call_ops = {o["name"] for o in catalog if o["mcp"] == "call"}
    assert not ({t.op for t in tools.values() if t.op} & call_ops - {"sequence.get"})


def _without(catalog, *names):
    return [o for o in catalog if o["name"] not in names and not o["name"].endswith(".estimate")]


def test_generic_tools_and_aliases(catalog):
    tools = build_tools(catalog)
    for name in ("sr_describe_ops", "sr_call", "sr_reload", "sr_job_wait", "sr_entity_get", "sr_entity_list",
                 "sr_entity_patch", "sr_history_undo", "sr_job_logs"):
        assert name in tools, name
    assert tools["sr_entity_patch"].op == "entity.patch"
    assert tools["sr_history_undo"].op == "history.undo"
    assert all(TOOL_NAME_RE.match(n) for n in tools)
    # Không có op ước tính / sequence.get → không đăng ký.
    bare = build_tools(_without(catalog, "sequence.get", "sequence.status"))
    assert "sr_estimate" not in bare
    assert "sr_sequence_status" not in bare


def test_estimate_and_sequence_status_registered_when_present(catalog):
    extra = [
        {"name": "job.estimate", "group": "job", "summary": "Ước tính", "mcp": "call", "read_only": True,
         "params_schema": {"type": "object", "properties": {"op": {"type": "string"}}}},
        {"name": "sequence.get", "group": "sequence", "summary": "Chi tiết sequence", "mcp": "call",
         "read_only": True, "params_schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
    ]
    tools = build_tools(_without(catalog, "sequence.get", "sequence.status") + extra)
    assert tools["sr_estimate"].extra["ops"] == ["job.estimate"]
    assert tools["sr_sequence_status"].op == "sequence.get"


def test_annotations(catalog):
    tools = build_tools(catalog)
    ro = tools["sr_project_get"].annotations
    assert ro["read_only_hint"] is True and ro["destructive_hint"] is False and ro["idempotent_hint"] is True
    w = tools["sr_project_update"].annotations
    assert w["read_only_hint"] is False and w["destructive_hint"] is False
    d = tools["sr_project_delete"].annotations
    assert d["destructive_hint"] is True
    assert tools["sr_provider_try_image"].annotations["open_world_hint"] is True


def test_descriptions_carry_safety_rules(catalog):
    tools = build_tools(catalog)
    assert "sr_project_delete_preview" in tools["sr_project_delete"].description
    assert "dry_run" in tools["sr_provider_try_image"].description
    assert "sr_job_wait" in tools["sr_llm_run"].description


def test_every_destructive_tool_has_preview_tool_or_op(catalog):
    tools = build_tools(catalog)
    names = {o["name"] for o in catalog}
    for t in tools.values():
        if t.annotations["destructive_hint"] and t.kind == "op":
            group = t.op.split(".", 1)[0]
            assert f"{group}.delete_preview" in names or f"{t.op}_preview" in names, t.name


def test_mapping_problems(catalog):
    assert mapping_problems(catalog) == []
    bad = [{"name": "x.wipe", "group": "x", "mcp": "tool", "destructive": True},
           {"name": "y." + "a" * 70, "group": "y", "mcp": "tool"}]
    probs = mapping_problems(bad)
    assert any("x.wipe: destructive" in p for p in probs)
    assert any("không hợp lệ" in p for p in probs)
