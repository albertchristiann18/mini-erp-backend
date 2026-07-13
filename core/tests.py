from django.contrib.auth import get_user_model
from django.test import TestCase

from core.factories import CompanyFactory, UserProfileFactory
from core.services.user_service import create_user as run

User = get_user_model()


class CreateUserScriptTest(TestCase):
    def test_create_user_new_user_creates_company_and_profile(self):
        run(username="newuser")
        user = User.objects.get(username="newuser")
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.is_staff)
        self.assertEqual(user.profile.role, "admin")
        self.assertEqual(user.profile.company.name, "Newuser")

    def test_create_user_explicit_role_sets_is_staff_false(self):
        run(username="csuser", role="cs")
        user = User.objects.get(username="csuser")
        self.assertFalse(user.is_staff)
        self.assertEqual(user.profile.role, "cs")

    def test_create_user_explicit_password_is_used(self):
        run(username="pwuser", password="MySecretPw123")
        user = User.objects.get(username="pwuser")
        self.assertTrue(user.check_password("MySecretPw123"))

    def test_create_user_explicit_company_name(self):
        run(username="anyuser", company="Acme Corp")
        user = User.objects.get(username="anyuser")
        self.assertEqual(user.profile.company.name, "Acme Corp")

    def test_create_user_existing_username_is_idempotent(self):
        user = User.objects.create_user(username="existinguser", password="original_pw")
        UserProfileFactory(user=user, role="admin")
        run(username="existinguser")
        self.assertEqual(User.objects.filter(username="existinguser").count(), 1)
        self.assertEqual(User.objects.get(username="existinguser").profile.role, "admin")
        self.assertTrue(User.objects.get(username="existinguser").check_password("original_pw"))

    def test_create_user_existing_user_company_mismatch_gets_synced(self):
        old_company = CompanyFactory(name="OldCo")
        profile = UserProfileFactory(user__username="mismatcheduser", company=old_company)
        run(username="mismatcheduser", company="NewCo")
        profile.refresh_from_db()
        self.assertEqual(profile.company.name, "NewCo")

    def test_create_user_default_company_name_is_capitalized_username(self):
        run(username="teststaff")
        user = User.objects.get(username="teststaff")
        self.assertEqual(user.profile.company.name, "Teststaff")

    def test_create_user_existing_user_role_change_is_ignored_and_noted(self):
        profile = UserProfileFactory(user__username="rolechangeuser", role="admin")
        run(username="rolechangeuser", role="finance")
        profile.refresh_from_db()
        self.assertEqual(profile.role, "admin")
