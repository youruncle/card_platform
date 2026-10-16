"""Pixel limits and display orientation for new capture/media routes only."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_MEDIA_PIXELS = 40_000_000


def _check_size(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    if min(width, height) < 2 or width * height > MAX_MEDIA_PIXELS:
        raise ValueError('image is invalid or exceeds 40 megapixels')
    if image.format not in {'JPEG', 'PNG', 'WEBP'}:
        raise ValueError('image must be JPEG, PNG, or WebP')
    return width, height


def validate_image_header(source: bytes | Path) -> tuple[int, int]:
    """Read encoded dimensions without allocating decoded pixels; return raw dimensions."""
    try:
        with Image.open(BytesIO(source) if isinstance(source, bytes) else source) as image:
            return _check_size(image)
    except Image.DecompressionBombError as error:
        raise ValueError('image exceeds 40 megapixels') from error
    except (OSError, SyntaxError) as error:
        raise ValueError('image header is invalid') from error


def normalize_media_content(content: bytes) -> tuple[bytes, int, int]:
    """Return processing bytes in display orientation, leaving stored upload bytes untouched.

    Transcode only when EXIF actually rotates/mirrors pixels. The processed PNG has no
    orientation tag, so OpenCV applies the transform exactly once for every format.
    """
    validate_image_header(content)
    try:
        with Image.open(BytesIO(content)) as image:
            width, height = _check_size(image)
            if image.getexif().get(274, 1) not in {2, 3, 4, 5, 6, 7, 8}:
                return content, width, height
            normalized = ImageOps.exif_transpose(image).convert('RGB')
            normalized.info.clear()
            output = BytesIO()
            normalized.save(output, format='PNG')
            return output.getvalue(), normalized.width, normalized.height
    except (OSError, SyntaxError) as error:
        raise ValueError('image cannot be decoded') from error
