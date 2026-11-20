"""Sinh frontend/openapi.tmp.json từ create_app() (không cần chạy server), rồi:
    cd frontend && npx openapi-typescript openapi.tmp.json --default-non-nullable false -o src/api/schema.d.ts && del openapi.tmp.json
Chạy: .venv\Scripts\python scripts\gen_openapi.py
"""

import json
import pathlib
import tempfile

from sagareel.app import create_app
from sagareel.config import load_settings

tmp = pathlib.Path(tempfile.mkdtemp())
app = create_app(load_settings(data_dir=tmp / "data", env_file=tmp / ".env.local"))
out = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "openapi.tmp.json"
out.write_text(json.dumps(app.openapi(), ensure_ascii=False), encoding="utf-8")
print(out, len(app.openapi()["paths"]), "paths")
