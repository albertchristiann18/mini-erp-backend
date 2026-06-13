import secrets
import string
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company, UserProfile

User = get_user_model()


def _random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Command(BaseCommand):
    help = "Create a new login user with their own isolated company (auto-generates password)"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--username", required=True, help="Login username")
        parser.add_argument(
            "--role",
            default="admin",
            choices=[c[0] for c in UserProfile.ROLE_CHOICES],
            help="UserProfile role (default: admin)",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        username: str = options["username"]
        role: str = options["role"]

        if User.objects.filter(username=username).exists():
            raise CommandError(f"User '{username}' already exists.")

        password = _random_password()
        email = f"{username}@example.com"
        company_name = username.capitalize()

        is_staff = role == "admin"

        company = Company.objects.create(name=company_name, email=email)
        user = User.objects.create_user(
            username=username, email=email, password=password, is_staff=is_staff
        )
        UserProfile.objects.create(user=user, company=company, role=role)

        self.stdout.write(self.style.SUCCESS("User created successfully"))
        self.stdout.write(f"  Username:   {username}")
        self.stdout.write(self.style.WARNING(f"  Password:   {password}"))
        self.stdout.write(f"  Email:      {email}")
        self.stdout.write(f"  Company:    {company_name} (ID: {company.id})")
        self.stdout.write(f"  Role:       {role}")
        self.stdout.write("")
        self.stdout.write("Save the password — it will not be shown again.")
