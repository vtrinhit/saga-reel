"""Thao tác nhóm `auth`, `token`, `session` (spec §5.7). Mọi op ở đây chỉ chủ sở hữu gọi được (`viewer=False`)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, RootModel

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import auth

RoleName = Literal["owner", "viewer"]


class SetPasswordParams(BaseModel):
    role: RoleName = Field(description="Vai trò: owner (chủ sở hữu) hoặc viewer (người xem)")
    password: str = Field(min_length=8, max_length=1024, description="Mật khẩu mới, tối thiểu 8 ký tự")
    current_password: str | None = Field(
        None,
        max_length=1024,
        description="Mật khẩu CHỦ SỞ HỮU hiện tại — bắt buộc khi vai trò này đã có mật khẩu (đổi mật khẩu)",
    )


class SetPasswordResult(BaseModel):
    role: RoleName
    revoked_sessions: int
    revoked_tokens: int


@op(
    "auth.set_password",
    summary=(
        "Đặt/đổi mật khẩu chủ sở hữu hoặc người xem (lần đầu chỉ từ máy cục bộ; đổi cần mật khẩu chủ hiện tại); "
        "thu hồi phiên và token API của vai trò đó"
    ),
    params=SetPasswordParams,
    result=SetPasswordResult,
    viewer=False,
)
async def auth_set_password(ctx: OpContext, p: SetPasswordParams) -> dict:
    return await auth.set_password(ctx, p.role, p.password, p.current_password)


class TokenInfo(BaseModel):
    id: int
    name: str
    role: RoleName
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool
    suspended: bool = Field(False, description="Agent đang tạm dừng (request nhận 403 token_suspended)")
    local_only: bool = Field(False, description="Token agent cục bộ của app (chỉ dùng trên máy này)")


class TokenCreated(TokenInfo):
    token: str = Field(description="Token thô — chỉ hiện MỘT lần, hãy lưu lại ngay")


class TokenList(RootModel[list[TokenInfo]]):
    pass


class TokenCreateParams(BaseModel):
    name: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
        description="Tên token (chữ, số, _ . -; tối đa 64 ký tự); actor trong lịch sử là `agent:<tên>`",
    )
    role: RoleName = "owner"
    expires_days: int | None = Field(None, ge=1, le=3650, description="Hết hạn sau số ngày này; bỏ trống = không hạn")


class AuthIdParams(BaseModel):
    id: int


class AuthNoParams(BaseModel):
    pass


@op(
    "token.create",
    summary="Tạo token API cho agent/CLI/MCP từ xa; token thô chỉ trả về một lần",
    params=TokenCreateParams,
    result=TokenCreated,
    viewer=False,
)
def token_create(ctx: OpContext, p: TokenCreateParams) -> dict:
    return auth.create_token(ctx, p.name, p.role, p.expires_days)


@op(
    "token.list",
    summary="Danh sách token API (không có token thô hay hash)",
    params=AuthNoParams,
    result=TokenList,
    read_only=True,
    idempotent=True,
    viewer=False,
)
def token_list(ctx: OpContext, p: AuthNoParams) -> list[dict]:
    return auth.list_tokens(ctx)


@op(
    "token.revoke",
    summary="Thu hồi một token API (dùng lại sẽ bị từ chối)",
    params=AuthIdParams,
    result=TokenInfo,
    idempotent=True,
    viewer=False,
)
def token_revoke(ctx: OpContext, p: AuthIdParams) -> dict:
    return auth.revoke_token(ctx, p.id)


class TokenNameParams(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="Tên token (agent `agent:<tên>`), vd. local-agent")


@op(
    "token.suspend",
    summary="Tạm dừng một agent: mọi token API tên này bị từ chối (403 token_suspended) tới khi bật lại",
    params=TokenNameParams,
    result=TokenList,
    idempotent=True,
    viewer=False,
)
def token_suspend(ctx: OpContext, p: TokenNameParams) -> list[dict]:
    return auth.set_suspended(ctx, p.name, True)


@op(
    "token.resume",
    summary="Bật lại agent đã tạm dừng (token API không tự bật lại được)",
    params=TokenNameParams,
    result=TokenList,
    idempotent=True,
    viewer=False,
)
def token_resume(ctx: OpContext, p: TokenNameParams) -> list[dict]:
    return auth.set_suspended(ctx, p.name, False)


class AgentsPauseOut(BaseModel):
    suspended: bool
    changed: int = Field(description="Số token đổi trạng thái")
    tokens: list[TokenInfo]


@op(
    "agents.pause_all",
    summary="Tạm dừng MỌI agent (mọi token API còn hiệu lực, kể cả agent cục bộ) — nút dừng khẩn",
    params=AuthNoParams,
    result=AgentsPauseOut,
    idempotent=True,
    viewer=False,
    mcp="tool",
)
def agents_pause_all(ctx: OpContext, p: AuthNoParams) -> dict:
    return auth.set_all_suspended(ctx, True)


@op(
    "agents.resume_all",
    summary="Bật lại mọi agent đã tạm dừng (chỉ chủ trong giao diện/máy cục bộ, không qua token API)",
    params=AuthNoParams,
    result=AgentsPauseOut,
    idempotent=True,
    viewer=False,
)
def agents_resume_all(ctx: OpContext, p: AuthNoParams) -> dict:
    return auth.set_all_suspended(ctx, False)


class SessionInfo(BaseModel):
    id: int
    role: RoleName
    created_at: datetime
    expires_at: datetime
    remember: bool
    ip: str
    user_agent: str
    revoked_at: datetime | None
    active: bool


class SessionList(RootModel[list[SessionInfo]]):
    pass


@op(
    "session.list",
    summary="Danh sách phiên đăng nhập (mới nhất trước)",
    params=AuthNoParams,
    result=SessionList,
    read_only=True,
    idempotent=True,
    viewer=False,
)
def session_list(ctx: OpContext, p: AuthNoParams) -> list[dict]:
    return auth.list_sessions(ctx)


@op(
    "session.revoke",
    summary="Thu hồi một phiên đăng nhập (thiết bị đó phải đăng nhập lại)",
    params=AuthIdParams,
    result=SessionInfo,
    idempotent=True,
    viewer=False,
)
def session_revoke(ctx: OpContext, p: AuthIdParams) -> dict:
    return auth.revoke_session(ctx, p.id)
