"""Tiện ích test của Task D (âm thanh): dựng sequence + clip trực tiếp trong DB (Task B chưa gộp), gọi op, chờ job."""

from __future__ import annotations

import anyio
from sagareel.core import ops, rank
from sagareel.db.models import Clip, Sequence

FAKE_TTS = {"kind": "tts", "adapter": "fake_tts", "name": "fake-tts"}


def make_sequence(database, project_id: int, n: int = 3, *, engine: str = "ai_video", key: str = "ep1",
                  duration_ms: int = 6000) -> tuple[int, list[int]]:
    with database.session() as s:
        seq = Sequence(project_id=project_id, key=key, number=1, title="Tập 1")
        s.add(seq)
        s.flush()
        clips = [Clip(sequence_id=seq.id, rank=r, key=f"c{i + 1}", engine=engine, duration_ms=duration_ms)
                 for i, r in enumerate(rank.n_after(None, n))]
        s.add_all(clips)
        s.flush()
        return seq.id, [c.id for c in clips]


def invoke(name: str, payload: dict, ctx):
    ops_load()
    return anyio.run(ops.invoke, name, payload, ctx)


def ops_load() -> None:
    from sagareel import ops as ops_pkg

    ops_pkg.load_all()


def rest(client, name: str, payload: dict | None = None) -> dict:
    r = client.post(f"/api/ops/{name}", json=payload or {})
    assert r.status_code == 200, r.text
    return r.json()


def rest_error(client, name: str, payload: dict) -> dict:
    r = client.post(f"/api/ops/{name}", json=payload)
    assert r.status_code >= 400, r.text
    return r.json()


def setup_fake_tts(client) -> None:
    rest(client, "provider.create", FAKE_TTS)
    rest(client, "settings.set_global", {"values": {"tts.connection": "fake-tts", "tts.voice": "fake-vi-female"}})


def wait_jobs(client, ids: list[int], timeout_s: int = 60) -> list[dict]:
    if not ids:
        return []
    body = rest(client, "job.wait", {"ids": ids, "timeout_s": timeout_s})
    assert not body["timed_out"], body
    return body["items"]
