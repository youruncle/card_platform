from io import BytesIO

import httpx
from PIL import Image
from pydantic import ValidationError
import pytest

from card_platform.mcp.schemas import McpFailure


def png():
    output = BytesIO()
    Image.new("RGB", (8, 12), "blue").save(output, "PNG")
    return output.getvalue()


class AttachmentStream(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        for offset in range(0, len(self.content), 65536):
            yield self.content[offset:offset + 65536]


def test_attachment_contract_declares_optional_metadata_and_requires_mapping():
    from card_platform.mcp.media import CardImportFilesRequest, ChatGPTFile
    schema = ChatGPTFile.model_json_schema()
    assert set(schema["properties"]) == {"download_url", "file_id", "mime_type", "file_name"}
    assert set(schema["required"]) == {"download_url", "file_id"}
    one = {"download_url": "https://files.example.invalid/card", "file_id": "file-1"}
    valid = {"files": [one], "sides": [{"side": "front", "group_id": "card-a"}],
             "idempotency_key": "b084fa78-bb4e-4d5a-b665-ceeb68b136a2"}
    assert CardImportFilesRequest(**valid).files[0].mime_type is None
    for changed in ({"files": []}, {"files": [one] * 21}, {"sides": []},
                    {"files": [one, one], "sides": [{"side": "front", "group_id": "same"}] * 2}):
        with pytest.raises(ValidationError):
            CardImportFilesRequest(**{**valid, **changed})
    with pytest.raises(ValidationError):
        CardImportFilesRequest(idempotency_key=valid["idempotency_key"], sides=valid["sides"])


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://8.8.8.8/a", "https://127.0.0.1/a", "https://10.0.0.1/a", "https://169.254.169.254/a",
    "https://[::1]/a", "https://[::ffff:127.0.0.1]/a", "https://192.0.2.1/a",
    "https://user:pass@8.8.8.8/a", "https://8.8.8.8:8443/a", "file:///etc/passwd",
])
async def test_private_reserved_or_non_https_attachment_rejected_before_network(url):
    from card_platform.mcp.media import AttachmentDownloader

    def network(request):
        pytest.fail("unsafe URL reached HTTP transport")
    with pytest.raises(McpFailure):
        await AttachmentDownloader(transport=httpx.MockTransport(network)).download(url)


@pytest.mark.asyncio
async def test_pins_validated_ip_and_tls_hostname_to_prevent_dns_change(monkeypatch, caplog):
    from card_platform.mcp import media

    addresses = iter([["8.8.8.8"], ["127.0.0.1"]])
    async def resolve(host):
        return next(addresses)
    monkeypatch.setattr(media, "resolve_addresses", resolve)

    def network(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "files.example.invalid"
        assert request.extensions["sni_hostname"] == "files.example.invalid"
        return httpx.Response(200, stream=AttachmentStream(png()), headers={"Content-Type": "image/png"})
    with caplog.at_level("INFO", logger="httpx"):
        result = await media.AttachmentDownloader(transport=httpx.MockTransport(network)).download(
            "https://files.example.invalid/a?secret-signed-download=do-not-log")
    assert result == png()
    assert "do-not-log" not in caplog.text
    assert "secret-signed-download" not in caplog.text


@pytest.mark.asyncio
async def test_every_redirect_is_validated_and_cannot_reach_internal_address():
    from card_platform.mcp.media import AttachmentDownloader

    def network(request):
        assert request.url.host == "8.8.8.8"
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})
    with pytest.raises(McpFailure):
        await AttachmentDownloader(transport=httpx.MockTransport(network)).download("https://8.8.8.8/a")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers,content", [
    (403, {}, b"expired"), (200, {"Content-Length": "10485761"}, b""),
    (200, {}, b"x" * (10 * 1024 * 1024 + 1)), (200, {"Content-Type": "image/png"}, b"not an image"),
    (200, {"Content-Encoding": "gzip"}, b""),
])
async def test_expired_oversized_compressed_or_invalid_files_are_not_accepted(status, headers, content):
    from card_platform.mcp.media import AttachmentDownloader
    def network(request):
        return httpx.Response(status, headers=headers, stream=AttachmentStream(content))
    with pytest.raises(McpFailure):
        await AttachmentDownloader(transport=httpx.MockTransport(network)).download("https://8.8.8.8/a")


@pytest.mark.asyncio
async def test_dns_mixed_public_and_private_answers_rejected(monkeypatch):
    from card_platform.mcp import media
    async def resolve(host):
        return ["8.8.8.8", "127.0.0.1"]
    monkeypatch.setattr(media, "resolve_addresses", resolve)
    def network(request):
        pytest.fail("mixed DNS answers reached network")
    with pytest.raises(McpFailure):
        await media.AttachmentDownloader(transport=httpx.MockTransport(network)).download("https://files.example.invalid/a")
