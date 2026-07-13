import secrets
import string
import sys
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from core.models import Company, UserProfile

User = get_user_model()


def _random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@transaction.atomic
def create_user(
    username: str,
    role: str = "admin",
    password: str | None = None,
    company: str | None = None,
    stdout: Any = None,
) -> None:
    if stdout is None:
        stdout = sys.stdout

    company_name = company or username.capitalize()
    is_staff = role == "admin"

    company_obj, company_created = Company.objects.get_or_create(
        name=company_name, defaults={"is_active": True}
    )
    stdout.write(
        f"{'Created' if company_created else 'Existing'} company: "
        f"{company_obj.name} (id={company_obj.id})\n"
    )

    user_created = False
    generated_password = password
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        generated_password = password or _random_password()
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=generated_password,
            is_staff=is_staff,
        )
        user_created = True

    if user_created:
        stdout.write(f"Created user: {username} / {generated_password}\n")
    else:
        stdout.write(f"Existing user: {username}\n")

    profile, profile_created = UserProfile.objects.get_or_create(
        user=user, defaults={"company": company_obj, "role": role}
    )
    if not profile_created and profile.company != company_obj:
        profile.company = company_obj
        profile.save(update_fields=["company"])

    # Role is intentionally NOT synced for existing users — role changes are out of scope for
    # this service (there is no change_user_role tool right now); surface it instead of ignoring
    # it silently.
    if not profile_created and profile.role != role:
        stdout.write(
            f"Note: '{username}' already has role '{profile.role}' — "
            f"requested role '{role}' was NOT applied (role changes are not supported by this service).\n"
        )

    stdout.write(
        f"{'Created' if profile_created else 'Existing'} profile — "
        f"role={profile.role}, company={profile.company.name}\n"
    )

    if user_created:
        stdout.write("\n")
        stdout.write("Save the password — it will not be shown again.\n")
