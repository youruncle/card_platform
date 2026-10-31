import base64
from io import BytesIO
from uuid import uuid4

from PIL import Image
from pydantic import ValidationError
import pytest

from card_platform.mcp.schemas import McpFailure
from test_mcp_portfolio import card_case as shared_card_case

card_case = shared_card_case

pytestmark = pytest.mark.asyncio


async def test_private_image_is_authorized_decodable_content_without_filesystem_path(card_case, tmp_path, monkeypatch):
    from card_platform.config import get_settings
    from card_platform.mcp.media import GetCardImageRequest, MediaTools

    tools, principal, ids, foreign = card_case
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    image = BytesIO()
    Image.new("RGB", (32, 48), "red").save(image, "JPEG")
    (tmp_path / "fixture.jpg").write_bytes(image.getvalue())
    async with tools.database.connection() as conn:
        cid = str(uuid4())
        await conn.execute("INSERT INTO chatgpt_oauth_client(client_id,metadata) VALUES (%s,'{}')", (cid,))
        await conn.execute("INSERT INTO chatgpt_connection(id,owner_id,client_id,scopes) VALUES (%s,%s,%s,'{cards:read}')",
                           (principal.connection_id, principal.owner_id, cid))
        await conn.execute("UPDATE collection_item SET image_front_url='/uploads/fixture.jpg' WHERE id=%s", (ids[0],))
    media = MediaTools(tools.database)
    try:
        result = await media.get_card_image(principal, GetCardImageRequest(item_id=ids[0]))
        assert result.content[0].type == "image"
        loaded = Image.open(BytesIO(base64.b64decode(result.content[0].data)))
        assert loaded.size == (32, 48)
        assert str(tmp_path) not in str(result.structuredContent)
        assert "fixture.jpg" not in str(result.structuredContent)
        for request in (GetCardImageRequest(item_id=foreign), GetCardImageRequest(item_id=ids[0], side="back")):
            with pytest.raises(McpFailure) as error:
                await media.get_card_image(principal, request)
            assert error.value.code == "NOT_FOUND"
        async with tools.database.connection() as conn:
            await conn.execute("UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s", (principal.connection_id,))
        with pytest.raises(McpFailure) as error:
            await media.get_card_image(principal, GetCardImageRequest(item_id=ids[0]))
        assert error.value.code == "AUTH_REQUIRED"
        with pytest.raises(ValidationError):
            GetCardImageRequest(item_id=ids[0], side="../../secret")
    finally:
        get_settings.cache_clear()
