"""
Seed script — create a test admin user + company so you can log in.

Usage:
    uv run python manage.py shell < scripts/seed_test_user.py
or:
    uv run python manage.py shell -c "exec(open('scripts/seed_test_user.py').read())"
"""

import django

django.setup()  # noqa: E402 — safe to call if already set up

from django.contrib.auth import get_user_model

from core.models import Company, UserProfile

User = get_user_model()

# ── Company ─────────────────────────────────────────────────────────────────
company, company_created = Company.objects.get_or_create(
    name="Mirako",
    defaults={"is_active": True},
)
print(f"{'Created' if company_created else 'Existing'} company: {company.name} (id={company.id})")

# ── Admin user ───────────────────────────────────────────────────────────────
USERNAME = "admin"
PASSWORD = "admin123"

user_created = False
if not User.objects.filter(username=USERNAME).exists():
    user = User.objects.create_superuser(
        username=USERNAME,
        email="albert12365@gmail.com",
        password=PASSWORD,
    )
    user_created = True
else:
    user = User.objects.get(username=USERNAME)

print(f"{'Created' if user_created else 'Existing'} user: {USERNAME} / {PASSWORD}")

# ── User profile ─────────────────────────────────────────────────────────────
profile, profile_created = UserProfile.objects.get_or_create(
    user=user,
    defaults={"company": company, "role": "admin"},
)
if not profile_created and profile.company != company:
    profile.company = company
    profile.save(update_fields=["company"])

print(
    f"{'Created' if profile_created else 'Existing'} profile — role={profile.role}, company={profile.company.name}"
)

print()
print("─" * 48)
print(f"  Login: {USERNAME} / {PASSWORD}")
print(f"  Company ID: {company.id}")
print("─" * 48)
