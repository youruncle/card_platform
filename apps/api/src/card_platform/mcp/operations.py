"""Confirmed operations whose effects and durable result share one transaction."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Awaitable, Callable
from uuid import UUID

from psycopg.types.json import Jsonb

from card_platform.mcp.schemas import McpFailure, McpPrincipal, StrictModel


def payload_digest(payload: dict, expected_revision: str) -> str:
    canonical = json.dumps({"payload": payload, "revision": expected_revision},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class OperationHandler:
    scopes: frozenset[str]
    snapshot: Callable[..., Awaitable[str]]
    execute: Callable[..., Awaitable[dict]]


class ExecuteOperationRequest(StrictModel):
    operation_id: UUID


class OperationService:
    def __init__(self, database, web_origin: str):
        self.database = database
        self.web_origin = web_origin.rstrip("/")
        self.handlers: dict[str, OperationHandler] = {}

    def register(self, kind: str, handler: OperationHandler):
        if kind in self.handlers:
            raise ValueError("operation handler already registered")
        self.handlers[kind] = handler

    def register_execution_tool(self, registry):
        from card_platform.mcp.server import ToolAdapter

        async def execute_tool(principal, request):
            return await self.execute(principal, request.operation_id)

        registry.register(ToolAdapter("execute_operation", "执行已经在平台确认页批准的操作编号，并回读保存结果。不能用confirmed布尔值或聊天口头批准代替平台确认。",
                          ExecuteOperationRequest, frozenset({"cards:read"}), execute_tool, read_only=False, destructive=True))

    def _handler(self, kind):
        if kind not in self.handlers:
            raise McpFailure("INVALID_INPUT", "此操作尚未启用", "查询当前可用能力")
        return self.handlers[kind]

    async def _grant(self, conn, principal, scopes):
        row = await (await conn.execute(
            """SELECT g.scopes FROM chatgpt_connection g JOIN app_user u ON u.id=g.owner_id
               WHERE g.id=%s AND g.owner_id=%s AND g.revoked_at IS NULL
               AND g.expires_at>now() AND u.disabled_at IS NULL FOR UPDATE OF g""",
            (principal.connection_id, principal.owner_id),
        )).fetchone()
        if row is None:
            raise McpFailure("AUTH_REQUIRED", "连接已失效", "重新连接卡牌管家")
        if not scopes <= principal.scopes or not scopes <= set(row["scopes"]):
            raise McpFailure("FORBIDDEN", "此操作缺少权限", "重新授权所需权限")

    def _public(self, row):
        return {"operation_id": str(row["id"]), "kind": row["kind"], "status": row["status"],
                "payload": row["payload"], "payload_hash": row["payload_hash"],
                "expected_revision": row["expected_revision"], "expires_at": row["expires_at"].isoformat(),
                "approval_url": f"{self.web_origin}/chatgpt/actions/{row['id']}",
                "result": row["result_ref"]}

    @staticmethod
    def _check_preview(row):
        if row["status"] in {"expired", "cancelled", "failed"} or row["expires_at"] <= datetime.now(timezone.utc):
            raise McpFailure("EXPIRED_OPERATION", "操作已失效", "重新准备并核对操作")
        if payload_digest(row["payload"], row["expected_revision"]) != row["payload_hash"]:
            raise McpFailure("REVISION_CONFLICT", "操作摘要已变化", "重新准备并核对操作")

    async def prepare(self, principal: McpPrincipal, kind: str, idempotency_key: UUID,
                      payload: dict, expected_revision: str):
        handler = self._handler(kind)
        digest = payload_digest(payload, expected_revision)
        async with self.database.connection() as conn:
            await self._grant(conn, principal, handler.scopes)
            existing = await (await conn.execute(
                "SELECT * FROM chatgpt_operation WHERE owner_id=%s AND kind=%s AND idempotency_key=%s",
                (principal.owner_id, kind, idempotency_key),
            )).fetchone()
            if existing:
                if existing["payload_hash"] != digest or existing["connection_id"] != principal.connection_id:
                    raise McpFailure("IDEMPOTENCY_CONFLICT", "同一请求编号已用于其他参数或连接")
                return self._public(existing)
            if await handler.snapshot(conn, principal, payload) != expected_revision:
                raise McpFailure("REVISION_CONFLICT", "目标已变化", "重新查询并准备操作")
            row = await (await conn.execute(
                """INSERT INTO chatgpt_operation(owner_id,connection_id,kind,idempotency_key,
                   payload,payload_hash,expected_revision) VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(owner_id,kind,idempotency_key) DO NOTHING RETURNING *""",
                (principal.owner_id, principal.connection_id, kind, idempotency_key,
                 Jsonb(payload), digest, expected_revision),
            )).fetchone()
            if row is None:
                raise McpFailure("IDEMPOTENCY_CONFLICT", "请求编号已被另一连接使用")
            await self._audit(conn, row, "prepare", "prepared")
        return self._public(row)

    async def lookup(self, principal, operation_id):
        async with self.database.connection() as conn:
            await self._grant(conn, principal, frozenset())
            row = await (await conn.execute(
                "SELECT * FROM chatgpt_operation WHERE id=%s AND owner_id=%s AND connection_id=%s",
                (UUID(str(operation_id)), principal.owner_id, principal.connection_id),
            )).fetchone()
        if row is None:
            raise McpFailure("NOT_FOUND", "操作不存在")
        return self._public(row)

    async def preview_for_user(self, owner_id, operation_id):
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                "SELECT * FROM chatgpt_operation WHERE id=%s AND owner_id=%s",
                (UUID(str(operation_id)), owner_id),
            )).fetchone()
            if row is None:
                raise McpFailure("NOT_FOUND", "操作不存在")
            principal = McpPrincipal(owner_id=owner_id, connection_id=row["connection_id"],
                                     scopes=self._handler(row["kind"]).scopes)
            await self._grant(conn, principal, self._handler(row["kind"]).scopes)
            if row["status"] != "succeeded":
                self._check_preview(row)
        return self._public(row)

    async def approve(self, owner_id, session_id, operation_id, digest, *, acknowledgements=frozenset()):
        # Only the platform's authenticated, CSRF-guarded API calls this method.
        # It is deliberately not an MCP tool.
        async with self.database.connection() as conn:
            initial = await (await conn.execute(
                "SELECT * FROM chatgpt_operation WHERE id=%s AND owner_id=%s",
                (UUID(str(operation_id)), owner_id),
            )).fetchone()
            if initial is None:
                raise McpFailure("NOT_FOUND", "操作不存在")
            handler = self._handler(initial["kind"])
            principal = McpPrincipal(owner_id=owner_id, connection_id=initial["connection_id"], scopes=handler.scopes)
            await self._grant(conn, principal, handler.scopes)
            row = await (await conn.execute("SELECT * FROM chatgpt_operation WHERE id=%s FOR UPDATE",
                                           (initial["id"],))).fetchone()
            self._check_preview(row)
            if row["payload_hash"] != digest:
                raise McpFailure("REVISION_CONFLICT", "批准的摘要与当前操作不一致")
            required = set(row["payload"].get("required_acknowledgements", []))
            if not required <= set(acknowledgements):
                raise McpFailure("CONFIRMATION_REQUIRED", "请先核对当前正背图片并确认配对", "在平台确认页完成图片核对")
            if await handler.snapshot(conn, principal, row["payload"]) != row["expected_revision"]:
                raise McpFailure("REVISION_CONFLICT", "目标已变化", "重新查询并准备操作")
            if row["status"] not in {"prepared", "approved"}:
                raise McpFailure("CONFIRMATION_REQUIRED", "操作状态不允许批准")
            row = await (await conn.execute(
                """UPDATE chatgpt_operation SET status='approved',approved_payload_hash=payload_hash,
                   approved_session_id=%s,approved_at=now() WHERE id=%s RETURNING *""",
                (session_id, row["id"]),
            )).fetchone()
            await self._audit(conn, row, "approve", "approved")
        return self._public(row)

    async def execute(self, principal, operation_id):
        async with self.database.connection() as conn:
            await self._grant(conn, principal, frozenset())
            row = await (await conn.execute(
                """SELECT * FROM chatgpt_operation WHERE id=%s AND owner_id=%s
                   AND connection_id=%s FOR UPDATE""",
                (UUID(str(operation_id)), principal.owner_id, principal.connection_id),
            )).fetchone()
            if row is None:
                raise McpFailure("NOT_FOUND", "操作不存在")
            handler = self._handler(row["kind"])
            await self._grant(conn, principal, handler.scopes)
            if row["status"] == "succeeded":
                return {"operation_id": str(row["id"]), "already_executed": True, "readback": row["result_ref"]}
            self._check_preview(row)
            if (row["status"] != "approved" or not row["approved_at"]
                    or row["approved_payload_hash"] != row["payload_hash"]):
                raise McpFailure("CONFIRMATION_REQUIRED", "请先在平台核对并批准操作",
                                 f"打开 {self.web_origin}/chatgpt/actions/{row['id']}")
            if await handler.snapshot(conn, principal, row["payload"]) != row["expected_revision"]:
                raise McpFailure("REVISION_CONFLICT", "确认后目标已变化", "重新查询并准备操作")
            await conn.execute("UPDATE chatgpt_operation SET status='executing' WHERE id=%s", (row["id"],))
            result = await handler.execute(conn, principal, row["payload"])
            await conn.execute(
                "UPDATE chatgpt_operation SET status='succeeded',result_ref=%s,completed_at=now() WHERE id=%s",
                (Jsonb(result), row["id"]),
            )
            await self._audit(conn, row, "execute", "succeeded")
        return {"operation_id": str(row["id"]), "already_executed": False, "readback": result}

    @staticmethod
    async def _audit(conn, row, event, code):
        await conn.execute(
            """INSERT INTO chatgpt_audit(owner_id,connection_id,event_type,target_id,payload_hash,result_code)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (row["owner_id"], row["connection_id"], event, row["id"], row["payload_hash"], code),
        )
