from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.thumbnail_settings import save_default_video_thumbnail


def _image_bytes(size=(1200, 800), color=(40, 100, 200), fmt="PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def test_uploaded_thumbnail_is_normalized_and_saved(tmp_path: Path):
    target = tmp_path / "assets" / "thumb.jpg"
    target.parent.mkdir()
    target.write_bytes(b"old-thumbnail")

    result = save_default_video_thumbnail(_image_bytes(), target)

    assert result == target.resolve()
    assert result.stat().st_size < 200_000
    with Image.open(result) as image:
        assert image.format == "JPEG"
        assert image.width <= 320 and image.height <= 320
    assert not list(target.parent.glob(".thumb-*.jpg"))


def test_invalid_image_does_not_replace_existing_thumbnail(tmp_path: Path):
    target = tmp_path / "thumb.jpg"
    target.write_bytes(b"preserve-me")
    try:
        save_default_video_thumbnail(b"not an image", target)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid image to be rejected")
    assert target.read_bytes() == b"preserve-me"
