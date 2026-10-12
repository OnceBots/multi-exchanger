from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 190_000
MAX_DIMENSION = 320


def save_default_video_thumbnail(image_bytes: bytes, destination: str | Path) -> Path:
    """Normalize an uploaded image and atomically replace the shared video thumbnail.

    The configured destination is shared by all child runtimes in this process.
    Atomic replacement means a concurrent upload cannot observe a half-written JPG.
    """
    if not image_bytes or len(image_bytes) > MAX_SOURCE_BYTES:
        raise ValueError("La imagen está vacía o supera el límite de 10 MB.")

    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".jpg", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            if image.width < 1 or image.height < 1:
                raise ValueError("La imagen no tiene dimensiones válidas.")

            encoded = False
            for quality in (88, 80, 72, 64, 56, 48, 40):
                image.save(temporary, format="JPEG", quality=quality, optimize=True, progressive=False)
                if temporary.stat().st_size < MAX_THUMBNAIL_BYTES:
                    encoded = True
                    break
            if not encoded:
                raise ValueError("No se pudo comprimir la miniatura por debajo de 190 KB.")

        if temporary.stat().st_size >= 200_000:
            raise ValueError("La miniatura debe pesar menos de 200 KB.")
        with Image.open(temporary) as check:
            if check.format != "JPEG" or not (0 < check.width <= MAX_DIMENSION and 0 < check.height <= MAX_DIMENSION):
                raise ValueError("La imagen no cumple los requisitos de Telegram (JPG, máximo 320×320).")
        os.replace(temporary, target)
        return target
    except (OSError, ValueError) as exc:
        raise ValueError(f"No se pudo guardar la miniatura: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
