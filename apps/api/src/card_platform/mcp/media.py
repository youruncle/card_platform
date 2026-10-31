import asyncio
import base64
from contextvars import ContextVar
from io import BytesIO
from ipaddress import ip_address, ip_network
import logging
import socket
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
import httpx
from mcp.types import CallToolResult, ImageContent, TextContent
from PIL import Image
from pydantic import Field, model_validator

from card_platform.capture_media_api import portfolio_image_path
from card_platform.image_service import detect_image_content_type
from card_platform.mcp.operations import OperationService
from card_platform.mcp.portfolio import card_revision
from card_platform.mcp.schemas import McpFailure, StrictModel
from card_platform.media_validation import validate_image_header

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_private_download = ContextVar("chatgpt_private_download", default=False)


class _PrivateDownloadLogFilter(logging.Filter):
    def filter(self, record):
        # httpx INFO logs request URLs, which contain short-lived signed secrets.
        # Suppress only this download task's records, including httpcore DEBUG.
        return not _private_download.get()


for _name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy"):
    logging.getLogger(_name).addFilter(_PrivateDownloadLogFilter())


class ChatGPTFile(StrictModel):
    download_url: str = Field(min_length=1, max_length=16384, repr=False)
    file_id: str = Field(min_length=1, max_length=512)
    mime_type: str | None = Field(default=None, max_length=128)
    file_name: str | None = Field(default=None, max_length=512)


class CaptureSide(StrictModel):
    side: Literal["front", "back", "auto"]
    group_id: str = Field(min_length=1, max_length=128)


class CardImportFilesRequest(StrictModel):
    files: list[ChatGPTFile] = Field(min_length=1, max_length=20)
    sides: list[CaptureSide] = Field(min_length=1, max_length=20)
    idempotency_key: UUID

    @model_validator(mode="after")
    def explicit_groups(self):
        if len(self.files) != len(self.sides):
            raise ValueError("每个附件必须有side/group映射")
        groups = {}
        for side in self.sides:
            groups.setdefault(side.group_id, []).append(side.side)
        if any(len(sides) > 1 and sorted(sides) != ["back", "front"] for sides in groups.values()):
            raise ValueError("同一group仅允许明确的一正一背，不能猜测多卡配对")
        return self


async def resolve_addresses(host: str) -> list[str]:
    result = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(row[4][0] for row in result))


def public_address(value):
    address = ip_address(value)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_loopback or address.is_link_local or address.is_unspecified):
        return False
    if address.version == 6:
        if address.ipv4_mapped is not None:
            return public_address(str(address.ipv4_mapped))
        # Translation/tunneling prefixes can route an apparently public IPv6
        # address to an internal IPv4 endpoint. They are unnecessary here.
        if any(address in ip_network(net) for net in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")):
            return False
    return True


class AttachmentDownloader:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def download(self, download_url: str) -> bytes:
        context = _private_download.set(True)
        try:
            async with asyncio.timeout(30):
                return await self._download(download_url)
        except McpFailure:
            raise
        except (httpx.HTTPError, httpx.InvalidURL, OSError, TimeoutError, ValueError):
            raise McpFailure("SOURCE_BLOCKED", "附件下载失败或已过期", "重新选择照片后重试") from None
        finally:
            _private_download.reset(context)

    async def _download(self, download_url):
        url = httpx.URL(download_url)
        for _ in range(4):
            if (url.scheme != "https" or not url.host or url.port not in {None, 443}
                    or url.username or url.password or url.fragment):
                raise McpFailure("INVALID_INPUT", "附件必须使用无凭证的HTTPS下载地址", "重新选择照片")
            try:
                ip_address(url.host)
                addresses = [url.host]
            except ValueError:
                addresses = await resolve_addresses(url.host)
            if not addresses or not all(public_address(address) for address in addresses):
                raise McpFailure("INVALID_INPUT", "附件地址未获允许", "重新选择照片")
            # Connect to the checked numeric IP; preserve the original hostname
            # for both Host and verified TLS SNI. No second DNS lookup occurs.
            pinned = url.copy_with(host=addresses[0])
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                    follow_redirects=False, timeout=httpx.Timeout(10, connect=5)) as client:
                async with client.stream("GET", pinned,
                    headers={"Host": url.host, "Accept-Encoding": "identity"},
                    extensions={"sni_hostname": url.host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise McpFailure("INVALID_INPUT", "附件重定向无效", "重新选择照片")
                        url = url.join(location)
                        continue
                    if response.status_code != 200:
                        raise McpFailure("SOURCE_BLOCKED", "附件下载失败或已过期", "重新选择照片后重试")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise McpFailure("INVALID_INPUT", "不支持压缩传输的附件", "重新选择照片")
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdecimal() or int(length) > MAX_ATTACHMENT_BYTES):
                        raise McpFailure("INVALID_INPUT", "附件超过10MiB或长度无效", "选择较小照片")
                    content = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        if len(content) + len(chunk) > MAX_ATTACHMENT_BYTES:
                            raise McpFailure("INVALID_INPUT", "附件超过10MiB", "选择较小照片")
                        content.extend(chunk)
            result = bytes(content)
            await asyncio.to_thread(self._validate_image, result)
            return result
        raise McpFailure("INVALID_INPUT", "附件重定向次数过多", "重新选择照片")

    @staticmethod
    def _validate_image(content):
        try:
            validate_image_header(content)
            with Image.open(BytesIO(content)) as image:
                image.load()
        except (ValueError, OSError, SyntaxError):
            raise McpFailure("INVALID_INPUT", "照片无法解码或超过像素预算", "选择有效JPEG、PNG或WebP照片") from None


class GetCardImageRequest(StrictModel):
    item_id: UUID
    side: Literal["front", "back"] = "front"
    variant: Literal["thumb", "preview", "original"] = "preview"


class MediaTools:
    def __init__(self, database):
        self.database = database

    async def get_card_image(self, principal, request: GetCardImageRequest):
        async with self.database.connection() as conn:
            await OperationService(self.database, "")._grant(conn, principal, frozenset({"cards:read"}))
            revision = await card_revision(conn, principal.owner_id, request.item_id)
        try:
            path = await portfolio_image_path(self.database, principal.owner_id, request.item_id,
                                               request.side, request.variant)
            content = await asyncio.to_thread(path.read_bytes)
        except HTTPException as error:
            raise McpFailure("NOT_FOUND" if error.status_code == 404 else "SOURCE_BLOCKED",
                             "图片不存在或暂时不可用", "重新查询该卡片图片") from None
        except OSError:
            raise McpFailure("NOT_FOUND", "图片不存在", "重新查询该卡片图片") from None
        # Recheck after derivative work. Revocation/deletion never grants access
        # merely because an authorized result existed in the local cache.
        async with self.database.connection() as conn:
            await OperationService(self.database, "")._grant(conn, principal, frozenset({"cards:read"}))
            if revision != await card_revision(conn, principal.owner_id, request.item_id):
                raise McpFailure("REVISION_CONFLICT", "读取期间卡片图片已变化", "重新查询图片")
        if len(content) > 10 * 1024 * 1024:
            raise McpFailure("INVALID_INPUT", "原图过大，请选择preview或thumb")
        return CallToolResult(content=[ImageContent(type="image", data=base64.b64encode(content).decode(),
                                mimeType=detect_image_content_type(content)),
                               TextContent(type="text", text=f"卡片{request.item_id}的{request.side}面")],
                              structuredContent={"item_id": str(request.item_id), "side": request.side,
                                                 "variant": request.variant, "revision": revision})

    def register(self, registry):
        from card_platform.mcp.server import ToolAdapter
        registry.register(ToolAdapter("get_card_image", "查看已消歧实卡的正面或背面私有图片。只接受我的item_id，默认preview。",
                          GetCardImageRequest, frozenset({"cards:read"}), self.get_card_image))
