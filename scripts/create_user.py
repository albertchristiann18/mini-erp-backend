"""
Create a login user with a company (idempotent — safe to re-run for an existing user)

Usage:
    uv run python scripts/create_user.py --username <name> [--role <role>] [--password <pw>] [--company <name>]
"""

import argparse
import os
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction

from core.models import Company, UserProfile

User = get_user_model()


def _random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@transaction.atomic
def run(
    username: str,
    role: str = "admin",
    password: str | None = None,
    company: str | None = None,
) -> None:
    company_name = company or username.capitalize()
    is_staff = role == "admin"

    company_obj, company_created = Company.objects.get_or_create(
        name=company_name, defaults={"is_active": True}
    )
    print(
        f"{'Created' if company_created else 'Existing'} company: "
        f"{company_obj.name} (id={company_obj.id})"
    )

    user_created = False
    generated_password = password
    if not User.objects.filter(username=username).exists():
        generated_password = password or _random_password()
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=generated_password,
            is_staff=is_staff,
        )
        user_created = True
    else:
        user = User.objects.get(username=username)

    if user_created:
        print(f"Created user: {username} / {generated_password}")
    else:
        print(f"Existing user: {username}")

    profile, profile_created = UserProfile.objects.get_or_create(
        user=user, defaults={"company": company_obj, "role": role}
    )
    if not profile_created and profile.company_id != company_obj.id:
        profile.company = company_obj
        profile.save(update_fields=["company"])

    # Role is intentionally NOT synced for existing users — role changes are out of scope for
    # this script (there is no change_user_role tool right now); surface it instead of ignoring
    # it silently.
    if not profile_created and profile.role != role:
        print(
            f"Note: '{username}' already has role '{profile.role}' — "
            f"requested role '{role}' was NOT applied (role changes are not supported by this script)."
        )

    print(
        f"{'Created' if profile_created else 'Existing'} profile — "
        f"role={profile.role}, company={profile.company.name}"
    )

    if user_created:
        print()
        print("Save the password — it will not be shown again.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a login user with a company")
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
    args = parser.parse_args()
    run(username=args.username, role=args.role, password=args.password, company=args.company)
