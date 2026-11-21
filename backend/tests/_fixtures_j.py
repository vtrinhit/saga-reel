"""Fixture Task J: LLM giả có kịch bản trả lời + ghi lại biến/tin nhắn đã render (không gọi provider thật)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from sagareel.core import templating
from sagareel.services import jobs, llm
from sagareel.services.llm import LLMOutcome, Route

JSON_TASKS = {t["key"] for t in llm.default_tasks() if t["json_mode"]}


class FakeLLM:
    """`replies[task]` = list (lần lượt, phần tử cuối dùng lại) hoặc hàm `(variables) -> reply`. Reply: dict/list (JSON),
    str (chữ; với task JSON: chuỗi không phải JSON → `llm_invalid_json` như `run_task` thật), Exception (ném)."""

    def __init__(self) -> None:
        self.replies: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    def on(self, task: str, *replies: Any) -> FakeLLM:
        self.replies[task] = list(replies)
        return self

    def on_fn(self, task: str, fn: Callable[[dict], Any]) -> FakeLLM:
        self.replies[task] = fn
        return self

    def calls_of(self, task: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["task"] == task]

    async def run_task(self, services, task_key, variables, *, images=(), override=None, on_delta=None,
                       on_attempt=None, log=None, project_id=None) -> LLMOutcome:
        from sagareel.services import prompts

        template_key = next(t["template_key"] for t in llm.default_tasks() if t["key"] == task_key)
        with services.db.session() as s:  # prompt thật của dự án (thiếu → prompt_missing như run_task thật)
            body = prompts.body(s, template_key, project_id)
        # template thật phải render được với biến đã truyền; tool tự nối khối định dạng đầu ra
        messages = llm.with_output_format(templating.render_messages(body, variables), template_key)
        n = len(self.calls_of(task_key))
        self.calls.append({"task": task_key, "variables": dict(variables), "messages": messages})
        spec = self.replies.get(task_key)
        if spec is None:
            raise AssertionError(f"FakeLLM: không có câu trả lời cho {task_key}")
        reply = spec(variables) if callable(spec) else spec[min(n, len(spec) - 1)]
        await asyncio.sleep(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, (dict, list)):
            text = json.dumps(reply, ensure_ascii=False)
            data = reply
        else:
            text = str(reply)
            data = None
            if task_key in JSON_TASKS:
                try:
                    data = llm.parse_json(text)
                except ValueError:
                    raise jobs.JobFailed("llm_invalid_json", "Câu trả lời không phải JSON hợp lệ (giả)") from None
        if on_delta is not None:
            on_delta(text)
        return LLMOutcome(text=text, data=data, route=Route(connection="fake"), attempts=[], usage={},
                          model="fake-llm")


def install(monkeypatch) -> FakeLLM:
    """Thay `services.llm.run_task` bằng LLM giả (dùng trong fixture `fake_llm` của từng file test)."""
    f = FakeLLM()
    monkeypatch.setattr(llm, "run_task", f.run_task)
    return f
