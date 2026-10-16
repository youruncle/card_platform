"""Serve portfolio images only to their current owner, without shared caching."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.config import get_settings

router = APIRouter(tags=["portfolio images"])
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"}


@router.api_route("/uploads/{filename:path}", methods=["GET", "HEAD"])
async def private_upload(
    filename: str,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> FileResponse:
    def not_found() -> HTTPException:
        return HTTPException(404, "图片不存在", headers=PRIVATE_HEADERS)

    # Uploaded images are flat files. Never expose directories, symlinks or arbitrary files.
    if not filename or filename in {".", ".."} or any(c in filename for c in ("/", "\\", "\x00")):
        raise not_found()
    root = get_settings().resolved_upload_dir
    path = root / filename
    if path.is_symlink() or path.resolve().parent != root.resolve() or not path.is_file():
        raise not_found()
    if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise not_found()
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            SELECT 1 FROM collection_item
            WHERE owner_id = %s AND deleted_at IS NULL
              AND (image_front_url = %s OR image_back_url = %s)
            LIMIT 1
            """,
            (user.id, f"/uploads/{filename}", f"/uploads/{filename}"),
        )
        if await result.fetchone() is None:
            raise not_found()
    return FileResponse(path, headers=PRIVATE_HEADERS)
