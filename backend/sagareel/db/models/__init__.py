"""Mọi model được import ở đây để `Base.metadata` đầy đủ (Alembic autogenerate dựa vào đó)."""

from sagareel.db.models.auth import ApiToken, AuthSecret, AuthSession
from sagareel.db.models.history import ChangeSet, Revision
from sagareel.db.models.jobs import Job, JobLog, Spend
from sagareel.db.models.media import Media
from sagareel.db.models.providers import LlmTask, ProviderConnection
from sagareel.db.models.system import GlobalSettings, SystemState, Template

__all__ = [
    "ApiToken",
    "AuthSecret",
    "AuthSession",
    "ChangeSet",
    "GlobalSettings",
    "Job",
    "JobLog",
    "LlmTask",
    "Media",
    "ProviderConnection",
    "Revision",
    "Spend",
    "SystemState",
    "Template",
]
