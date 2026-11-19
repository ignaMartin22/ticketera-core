import hashlib
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from catalog.models import ApiToken


class Command(BaseCommand):
    help = "Crea una clave de la API de gestión para un usuario. Se muestra una sola vez."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email (o usuario) del miembro del boliche.")

    def handle(self, *args, **options):
        email = options["email"].strip()
        users = get_user_model().objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
        if users.count() != 1:
            raise CommandError(f"Se esperaba un solo usuario con el email «{email}» y hay {users.count()}.")
        key = secrets.token_urlsafe(32)
        ApiToken.objects.create(
            key_hash=hashlib.sha256(key.encode()).hexdigest(), hint=key[-6:], user=users.get(),
        )
        self.stdout.write(self.style.SUCCESS(f"Clave de API para {email}:\n{key}"))
