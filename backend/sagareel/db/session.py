"""Engine SQLite, phiên làm việc và chạy migration Alembic theo cách lập trình."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
)


def _set_sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    try:
        for pragma in _PRAGMAS:
            cur.execute(pragma)
    finally:
        cur.close()


def sqlite_url(db_path: Path) -> str:
    return f"sqlite:///{Path(db_path).resolve().as_posix()}"


def alembic_config(url: str) -> Config:
    """Config Alembic dựng bằng code: chạy được từ mọi thư mục, không cần alembic.ini."""
    cfg = Config()
    # set_main_option nội suy kiểu ConfigParser → phải thoát ký tự '%'.
    cfg.set_main_option("script_location", MIGRATIONS_DIR.as_posix().replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.url = sqlite_url(self.db_path)
        # check_same_thread=False: lifespan, request và worker (to_thread) dùng chung pool ở nhiều luồng.
        self.engine: Engine = create_engine(self.url, connect_args={"check_same_thread": False})
        event.listen(self.engine, "connect", _set_sqlite_pragmas)
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Phiên DB: commit khi khối kết thúc bình thường, rollback khi có lỗi."""
        s = self._sessionmaker()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def migrate(self) -> None:
        """`alembic upgrade head` trên chính engine này (idempotent)."""
        cfg = alembic_config(self.url)
        with self.engine.connect() as conn:
            # Migration batch của SQLite tạo lại bảng (DROP + RENAME): tắt khoá ngoại để DROP không kích hoạt
            # ON DELETE CASCADE. PRAGMA này chỉ có tác dụng ngoài transaction nên chạy trước khi bắt đầu.
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, "head")
                conn.commit()
            finally:
                conn.rollback()
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                conn.commit()

    def dispose(self) -> None:
        self.engine.dispose()
