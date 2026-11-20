import shutil
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from . import covers
from .models import Event, Membership, TicketType, Venue
from .test_manage import auth, create_token

MEDIA = tempfile.mkdtemp(prefix="ticketera-media-")


def image_file(fmt="JPEG", size=(2400, 3000), name="flyer.jpg", mode="RGB"):
    out = BytesIO()
    Image.new(mode, size, "magenta").save(out, fmt)
    return SimpleUploadedFile(name, out.getvalue(), content_type=f"image/{fmt.lower()}")


@override_settings(MEDIA_ROOT=MEDIA, PUBLIC_API_URL="https://api.example.com")
class EventCoverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.moon = Venue.objects.create(slug="moon", name="Moon", is_active=True)
        cls.other = Venue.objects.create(slug="other", name="Otro", is_active=True)
        User = get_user_model()
        cls.admin = User.objects.create_user(username="dueno@moon.com")
        cls.door = User.objects.create_user(username="puerta@moon.com")
        Membership.objects.create(venue=cls.moon, user=cls.admin, role=Membership.Role.ADMIN)
        Membership.objects.create(venue=cls.moon, user=cls.door, role=Membership.Role.DOOR)
        cls.key = create_token(cls.admin)
        cls.door_key = create_token(cls.door)
        cls.event = Event.objects.create(
            venue=cls.moon, slug="noche", title="Noche",
            starts_at=timezone.now() + timedelta(days=5), is_published=True,
        )
        TicketType.objects.create(event=cls.event, slug="general-1", name="General", price=5000, capacity=100)
        cls.foreign = Event.objects.create(
            venue=cls.other, slug="ajena", title="Ajena",
            starts_at=timezone.now() + timedelta(days=5), is_published=True,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def url(self, venue="moon", event="noche"):
        return reverse("manage-event-cover", args=[venue, event])

    def upload(self, file, key=None, venue="moon", event="noche"):
        return self.client.post(self.url(venue, event), {"file": file}, headers=auth(key or self.key))

    def stored(self):
        self.event.refresh_from_db()
        return Path(MEDIA) / self.event.cover

    def test_upload_resizes_to_webp_and_shows_in_catalog(self):
        response = self.upload(image_file())
        self.assertEqual(response.status_code, 200)
        url = response.json()["cover"]
        self.assertTrue(url.startswith("https://api.example.com/media/eventos/moon/noche-"))
        self.assertTrue(url.endswith(".webp"))

        path = self.stored()
        with Image.open(path) as saved:
            self.assertEqual(saved.format, "WEBP")
            self.assertEqual(max(saved.size), covers.MAX_SIDE)
            self.assertEqual(saved.size, (1280, 1600))

        public = self.client.get(reverse("event-detail", args=["moon", "noche"])).json()
        self.assertEqual(public["event"]["cover"], url)
        managed = self.client.get(
            reverse("manage-event-detail", args=["moon", "noche"]), headers=auth(self.key)
        ).json()
        self.assertEqual(managed["event"]["cover"], url)

    def test_media_is_served_with_long_cache(self):
        self.upload(image_file())
        self.event.refresh_from_db()
        response = self.client.get(f"/media/{self.event.cover}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertIn("immutable", response["Cache-Control"])
        # Nada fuera de la carpeta de archivos subidos.
        self.assertIn(self.client.get("/media/../settings.py").status_code, (400, 404))
        self.assertIn(self.client.get("/media/%2e%2e/manage.py").status_code, (400, 404))

    def test_replacing_deletes_previous_file(self):
        self.upload(image_file())
        first = self.stored()
        self.upload(image_file("PNG", (800, 600), "flyer.png"))
        second = self.stored()
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_small_png_with_transparency_is_kept_small(self):
        response = self.upload(image_file("PNG", (400, 500), "logo.png", mode="RGBA"))
        self.assertEqual(response.status_code, 200)
        with Image.open(self.stored()) as saved:
            self.assertEqual(saved.size, (400, 500))

    def test_delete_removes_cover(self):
        self.upload(image_file())
        path = self.stored()
        response = self.client.delete(self.url(), headers=auth(self.key))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cover": None})
        self.event.refresh_from_db()
        self.assertEqual(self.event.cover, "")
        self.assertFalse(path.exists())

    def test_deleting_event_removes_its_cover(self):
        event = Event.objects.create(
            venue=self.moon, slug="sin-ventas", title="Sin ventas",
            starts_at=timezone.now() + timedelta(days=9),
        )
        self.upload(image_file(), event="sin-ventas")
        event.refresh_from_db()
        path = Path(MEDIA) / event.cover
        self.assertTrue(path.exists())
        response = self.client.delete(
            reverse("manage-event-detail", args=["moon", "sin-ventas"]), headers=auth(self.key)
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(path.exists())

    def test_rejects_non_images_and_other_formats(self):
        text = SimpleUploadedFile("flyer.jpg", b"no soy una imagen", content_type="image/jpeg")
        self.assertEqual(self.upload(text).json(), {"error": "invalid_cover"})
        gif = image_file("GIF", (100, 100), "flyer.gif", mode="P")
        self.assertEqual(self.upload(gif).json(), {"error": "invalid_cover"})
        self.assertEqual(
            self.client.post(self.url(), {}, headers=auth(self.key)).json(), {"error": "invalid_cover"}
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.cover, "")

    def test_rejects_too_big(self):
        original = covers.MAX_UPLOAD_BYTES
        covers.MAX_UPLOAD_BYTES = 1000
        try:
            response = self.upload(image_file())
        finally:
            covers.MAX_UPLOAD_BYTES = original
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": "cover_too_large"})

    def test_rejects_huge_dimensions(self):
        original = covers.MAX_SOURCE_PIXELS
        covers.MAX_SOURCE_PIXELS = 1000
        try:
            response = self.upload(image_file(size=(100, 100)))
        finally:
            covers.MAX_SOURCE_PIXELS = original
        self.assertEqual(response.status_code, 413)

    def test_only_venue_admin(self):
        self.assertEqual(self.client.post(self.url(), {"file": image_file()}).status_code, 401)
        self.assertEqual(self.upload(image_file(), key=self.door_key).status_code, 403)
        self.assertEqual(self.upload(image_file(), venue="other", event="ajena").status_code, 403)
        self.assertEqual(self.upload(image_file(), event="no-existe").status_code, 404)
        self.assertEqual(self.client.get(self.url(), headers=auth(self.key)).status_code, 405)

    def test_event_without_cover_is_null(self):
        public = self.client.get(reverse("event-detail", args=["moon", "noche"])).json()
        self.assertIsNone(public["event"]["cover"])
