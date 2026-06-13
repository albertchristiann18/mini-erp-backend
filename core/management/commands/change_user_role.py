from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import UserProfile

User = get_user_model()

ROLES = [choice[0] for choice in UserProfile.ROLE_CHOICES]


class Command(BaseCommand):
    help = "Change the role of an existing user"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--username", required=True, help="Username of the user to update")
        parser.add_argument(
            "--role", required=True, choices=ROLES, help=f"New role. Choices: {ROLES}"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        username: str = options["username"]
        new_role: str = options["role"]

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' does not exist.")

        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            raise CommandError(f"User '{username}' has no UserProfile.")

        old_role = profile.role
        profile.role = new_role
        profile.save(update_fields=["role", "udate"])

        user.is_staff = new_role == "admin"
        user.save(update_fields=["is_staff"])

        self.stdout.write(self.style.SUCCESS(f"Updated '{username}': {old_role} -> {new_role}"))
