from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from uuid import UUID


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class McpPrincipal(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: UUID
    connection_id: UUID
    scopes: frozenset[str]

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value):
        if not value or not value <= {"cards:read", "cards:write", "transactions:write", "research:run"}:
            raise ValueError("unsupported scope")
        return value


class McpFailure(Exception):
    """Safe, user actionable error. Never includes SQL or provider exceptions."""

    def __init__(self, code: str, message: str, recovery: str = "重新查询并核对后重试"):
        self.code = code
        self.message = message
        self.recovery = recovery
        super().__init__(message)

    def public(self) -> dict:
        return McpError(code=self.code, message=self.message, recovery=self.recovery).model_dump()


class McpError(StrictModel):
    code: Literal[
        "AUTH_REQUIRED", "FORBIDDEN", "NOT_FOUND", "INVALID_INPUT", "REVISION_CONFLICT",
        "CONFIRMATION_REQUIRED", "EXPIRED_OPERATION", "IDEMPOTENCY_CONFLICT",
        "SOURCE_BLOCKED", "INSUFFICIENT_EVIDENCE", "RETRY_EXHAUSTED",
    ]
    message: str
    recovery: str
