from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.models import UserProfile
from core.services.user_service import create_user


class Command(BaseCommand):
    help = "Create a login user with a company (idempotent)"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--username", required=True, help="Login username")
        parser.add_argument(
            "--role",
            default="admin",
            choices=[c[0] for c in UserProfile.ROLE_CHOICES],
            help="UserProfile role (default: admin)",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Password for a newly-created user (default: random secure password)",
        )
        parser.add_argument(
            "--company",
            default=None,
            help="Company name (default: capitalized username)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        username: str = options["username"]
        role: str = options["role"]
        password: str | None = options["password"]
        company: str | None = options["company"]

        try:
            create_user(
                username=username,
                role=role,
                password=password,
                company=company,
                stdout=self.stdout,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
