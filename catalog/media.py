from django.conf import settings
from django.views.decorators.http import require_safe
from django.views.static import serve


@require_safe
def serve_media(request, path):
    """Sirve las portadas subidas. El nombre cambia con cada subida, así que se cachean para siempre.

    Alcanza mientras el front (Railway) optimiza y cachea las imágenes; si el tráfico crece, que las
    sirva Caddy directo desde el volumen.
    """
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    return response
