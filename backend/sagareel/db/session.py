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


# Execution option đánh dấu connection cần khoá ghi ngay khi bắt đầu transaction.
IMMEDIATE_OPTION = "sagareel_begin_immediate"


def _begin_immediate(conn) -> None:
    """Listener `begin`: với connection mang `IMMEDIATE_OPTION`, mở transaction bằng `BEGIN IMMEDIATE`.

    pysqlite (chế độ mặc định) tự phát `BEGIN` hoãn ngay trước câu DML đầu tiên, nên các câu SELECT trước đó nằm ngoài
    transaction. Mở sẵn `BEGIN IMMEDIATE` ở đây thì pysqlite thấy đã có transaction và không phát BEGIN nữa;
    `commit()`/`rollback()` của pysqlite vẫn kết thúc đúng transaction này. Connection thường không bị ảnh hưởng
    (vẫn hoãn), và `migrate()` (PRAGMA ngoài transaction) giữ nguyên hành vi. Chờ khoá tối đa `busy_timeout`.
    """
    if conn.get_execution_options().get(IMMEDIATE_OPTION):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


# Bảng không có model: bảng ảo FTS5 `bible_fts` (migration 0004) và các bảng bóng của nó (`bible_fts_data`…).
UNMODELED_TABLE_PREFIXES = ("bible_fts",)


def include_name(name, type_, parent_names) -> bool:
    """Bộ lọc Alembic (`include_name`): so schema/autogenerate bỏ qua bảng không có model (không sinh `drop_table`
    cho bảng ảo FTS5)."""
    return not (type_ == "table" and name is not None and name.startswith(UNMODELED_TABLE_PREFIXES))


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
        # hide_parameters=True: lỗi SQL (IntegrityError/OperationalError…) không in tham số bind — có thể là khoá API.
        self.engine: Engine = create_engine(
            self.url, connect_args={"check_same_thread": False}, hide_parameters=True
        )
        event.listen(self.engine, "connect", _set_sqlite_pragmas)
        event.listen(self.engine, "begin", _begin_immediate)
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._immediate_sessionmaker = sessionmaker(
            bind=self.engine.execution_options(**{IMMEDIATE_OPTION: True}), expire_on_commit=False
        )

    @contextmanager
    def session(self, immediate: bool = False) -> Iterator[Session]:
        """Phiên DB: commit khi khối kết thúc bình thường, rollback khi có lỗi.

        `immediate=True`: transaction mở bằng `BEGIN IMMEDIATE` ở câu lệnh đầu tiên → giữ khoá ghi từ lúc đọc, các
        phiên ghi khác chờ (busy_timeout) tới khi phiên này kết thúc. Dùng cho đường ghi kiểu "đọc–kiểm tra–ghi"
        (UnitOfWork). Phiên thường giữ chế độ hoãn của pysqlite.
        """
        s = (self._immediate_sessionmaker if immediate else self._sessionmaker)()
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
