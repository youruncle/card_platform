"""One Streamable HTTP endpoint, with the parent API owning its lifespan.

Business tools are registered only when their authenticated adapters are ready.
The SDK manages the transport and protocol; no second database or worker is started.
"""

from dataclasses import dataclass
import json
import logging
from typing import Awaitable, Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl, ValidationError

from card_platform.config import get_settings
from card_platform.mcp.auth import CardOAuthProvider, OAuthConfig, require_principal
from card_platform.mcp.oauth_api import create_oauth_app
from card_platform.mcp.schemas import McpFailure, StrictModel
from card_platform.mcp.portfolio import PortfolioTools
from card_platform.mcp.media import MediaTools
from card_platform.mcp.captures import CaptureTools
from card_platform.mcp.operations import OperationService
from card_platform.mcp.mutations import MutationTools
from card_platform.mcp.reports import ReportTools
from card_platform.mcp.research import ResearchTools
from card_platform.mcp.jobs import JobTools
from card_platform.mcp.purchase_cost import PurchaseLookupTools

logger = logging.getLogger(__name__)


class EmptyRequest(StrictModel):
    pass


@dataclass(frozen=True)
class ToolAdapter:
    name: str
    description: str
    request_model: type[StrictModel]
    scopes: frozenset[str]
    handler: Callable[..., Awaitable[dict]]
    read_only: bool = True
    open_world: bool = False
    meta: dict | None = None
    destructive: bool = False


class ToolRegistry:
    def __init__(self, server, provider):
        self.provider = provider
        self.tools: dict[str, ToolAdapter] = {}

        @server._mcp_server.list_tools()
        async def list_tools():
            return [Tool(name=t.name, description=t.description,
                         inputSchema=t.request_model.model_json_schema(),
                         annotations=ToolAnnotations(readOnlyHint=t.read_only,
                            destructiveHint=t.destructive, idempotentHint=True, openWorldHint=t.open_world),
                         _meta={"securitySchemes": [{"type": "oauth2", "scopes": sorted(t.scopes)}],
                                **(t.meta or {})}) for t in self.tools.values()]

        @server._mcp_server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            return await self.invoke(name, arguments)

    def register(self, adapter: ToolAdapter):
        if adapter.name in self.tools:
            raise ValueError("tool already registered")
        self.tools[adapter.name] = adapter

    async def invoke(self, name, arguments):
        principal = None
        try:
            tool = self.tools.get(name)
            if tool is None:
                raise McpFailure("INVALID_INPUT", "工具未启用", "查询get_capabilities")
            current = get_access_token()
            verified = await self.provider.verify_token(current.token) if current else None
            principal = require_principal(verified, set(tool.scopes), self.provider.config)
            try:
                request = tool.request_model.model_validate(arguments)
            except ValidationError:
                raise McpFailure("INVALID_INPUT", "参数缺失或不符合工具要求", "核对工具参数后重试") from None
            result = await tool.handler(principal, request)
            await self._audit(principal, name, "ok")
            if isinstance(result, CallToolResult):
                return result
            normalized = json.loads(json.dumps(result, default=str, ensure_ascii=False, allow_nan=False))
            return CallToolResult(structuredContent=normalized, content=[TextContent(
                type="text", text=json.dumps(normalized, ensure_ascii=False))])
        except McpFailure as error:
            if principal:
                await self._audit(principal, name, error.code)
            return CallToolResult(isError=True, structuredContent=error.public(),
                                 content=[TextContent(type="text", text=error.message + "；" + error.recovery)])
        except Exception as error:
            # Do not log arguments, exception text, tokens, short-lived URLs or SQL.
            logger.error("MCP tool failed: tool=%s exception_type=%s", name, type(error).__name__)
            safe = McpFailure("SOURCE_BLOCKED", "服务暂时不可用", "稍后使用原请求编号重试")
            return CallToolResult(isError=True, structuredContent=safe.public(),
                                 content=[TextContent(type="text", text=safe.message)])

    async def _audit(self, principal, name, result_code):
        async with self.provider.database.connection() as conn:
            await conn.execute(
                """INSERT INTO chatgpt_audit(owner_id,connection_id,event_type,result_code)
                   VALUES (%s,%s,%s,%s)""",
                (principal.owner_id, principal.connection_id, f"tool:{name}", result_code),
            )

    def register_profile_tools(self):
        async def capabilities(principal, request):
            return {"schema_version": "028.1", "enabled_tools": list(self.tools),
                    "scopes": sorted(principal.scopes), "limits": {"page_size": 100, "files": 20}}

        async def profile(principal, request):
            async with self.provider.database.connection() as conn:
                row = await (await conn.execute(
                    "SELECT id,display_name,base_currency FROM app_user WHERE id=%s AND disabled_at IS NULL",
                    (principal.owner_id,),
                )).fetchone()
            if row is None:
                raise McpFailure("AUTH_REQUIRED", "账户已失效", "重新连接卡牌管家")
            return {"owner_id": str(row["id"]), "display_name": row["display_name"],
                    "base_currency": row["base_currency"], "scopes": sorted(principal.scopes),
                    "connection_id": str(principal.connection_id)}

        for name, handler, description in (
            ("get_capabilities", capabilities, "查询当前实际启用的卡牌管家工具和限制。未列出的能力不能调用。"),
            ("get_profile", profile, "核对当前连接的卡库账户、显示名和授权范围。不能指定其他账户。"),
        ):
            self.register(ToolAdapter(name, description, EmptyRequest, frozenset({"cards:read"}), handler))


def create_mcp_server(provider=None, **kwargs) -> FastMCP:
    kwargs.setdefault("transport_security", TransportSecuritySettings(
        allowed_hosts=["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "cardsp.cn", "api:8000"],
        allowed_origins=["http://localhost:*", "https://cardsp.cn", "https://chatgpt.com"],
    ))
    if provider is not None:
        kwargs.update(token_verifier=provider, auth=AuthSettings(
            issuer_url=AnyHttpUrl(provider.config.issuer),
            resource_server_url=AnyHttpUrl(provider.config.resource),
            validate_token_resource=True,
            required_scopes=["cards:read"],
        ))
    server = FastMCP(
        "Card Manager",
        instructions=(
            "使用已启用工具管理当前授权账户。先查询和消歧；关键变更先预览、"
            "在平台确认后执行并回读。未知身份或价格保持未知。"
        ),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        **kwargs,
    )
    if provider is not None:
        server.card_tools = ToolRegistry(server, provider)
        server.card_tools.register_profile_tools()
    return server


settings = get_settings()
oauth_provider = CardOAuthProvider(None, OAuthConfig(
    issuer=settings.chatgpt_oauth_issuer_url, resource=settings.chatgpt_mcp_resource_url,
    web_origin=settings.chatgpt_web_origin, redirect_uris=tuple(settings.chatgpt_oauth_redirect_uris),
))
oauth_app = create_oauth_app(oauth_provider)
def create_authenticated_runtime(database, operations=None):
    # The SDK session manager is single-use. Each parent lifespan, including a
    # TestClient restart, must get a fresh server and transport manager.
    server = create_mcp_server(oauth_provider)
    PortfolioTools(database).register(server.card_tools)
    MediaTools(database).register(server.card_tools)
    operations = operations or OperationService(database, oauth_provider.config.web_origin)
    server.capture_tools = CaptureTools(database, get_settings(), operations)
    server.capture_tools.register(server.card_tools, include_job_status=False)
    MutationTools(database, operations).register(server.card_tools)
    ReportTools(database).register(server.card_tools)
    server.research_tools = ResearchTools(database, get_settings(), operations)
    server.research_tools.register(server.card_tools)
    JobTools(database, server.capture_tools, server.research_tools).register(server.card_tools)
    server.purchase_lookup_tools = PurchaseLookupTools(database, operations)
    server.purchase_lookup_tools.register(server.card_tools)
    operations.register_execution_tool(server.card_tools)
    return server, server.streamable_http_app()


class MountedMcpApplication:
    async def __call__(self, scope, receive, send):
        await scope["app"].state.chatgpt_mcp_http(scope, receive, send)


mcp_app = MountedMcpApplication()
