"""Portada (flyer) de cada fecha: se sube desde la API de gestión, se achica a WebP y se guarda en MEDIA_ROOT.

Se guarda con un nombre nuevo en cada subida (lleva un sufijo al azar): así la URL cambia cuando
cambia la imagen y se puede cachear para siempre. La anterior se borra después de guardar.
"""

import secrets
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps, UnidentifiedImageError

# Lo que se acepta subir y cómo se guarda.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Una foto de celu grande ronda los 50 MP; más que esto no es un flyer y gastaría memoria al abrirlo.
MAX_SOURCE_PIXELS = 60_000_000
MAX_SIDE = 1600
WEBP_QUALITY = 82


def cover_url(path):
    """URL pública de la portada, o None. Con PUBLIC_API_URL es absoluta; si no, relativa al back."""
    if not path:
        return None
    return f"{settings.PUBLIC_API_URL}{settings.MEDIA_URL}{path}"


def process_cover(upload):
    """Valida la imagen subida y devuelve los bytes del WebP achicado. Errores: ValidationError con el código."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError("cover_too_large")
    try:
        with Image.open(upload) as image:
            if image.format not in ALLOWED_FORMATS:
                raise ValidationError("invalid_cover")
            width, height = image.size
            if width * height > MAX_SOURCE_PIXELS:
                raise ValidationError("cover_too_large")
            # En JPEG, decodifica directo a una escala menor: ahorra memoria y tiempo.
            image.draft("RGB", (MAX_SIDE, MAX_SIDE))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            out = BytesIO()
            image.save(out, "WEBP", quality=WEBP_QUALITY, method=4)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ValidationError("invalid_cover") from exc
    return out.getvalue()


def save_cover(event, data):
    """Guarda los bytes como portada nueva de la fecha y devuelve la ruta relativa a MEDIA_ROOT."""
    name = f"eventos/{event.venue.slug}/{event.slug}-{secrets.token_hex(4)}.webp"
    return default_storage.save(name, ContentFile(data))


def delete_cover_file(path):
    if path:
        default_storage.delete(path)
