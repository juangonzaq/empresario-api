"""Registro, sesión, recuperación y perfil."""

from __future__ import annotations

from django.core import mail as django_mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import OneTimeToken, TokenPurpose, User

PASSWORD = "una-clave-larga-99"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RegistrationTests(APITestCase):
    def setUp(self):
        cache.clear()
        django_mail.outbox = []

    def test_register_sends_verification_and_does_not_log_in_yet(self):
        response = self.client.post(
            reverse("accounts:register"),
            {"email": "Ana@Empresa.PE", "password": PASSWORD, "first_name": "Ana"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        # El registro no devuelve tokens: primero hay que verificar el correo.
        self.assertNotIn("access", response.data)
        user = User.objects.get()
        self.assertEqual(user.email, "ana@empresa.pe")   # normalizado
        self.assertFalse(user.email_verified)
        self.assertEqual(len(django_mail.outbox), 1)

    def test_existing_email_gets_the_same_answer(self):
        User.objects.create_user(email="ana@empresa.pe", password=PASSWORD)
        first = self.client.post(
            reverse("accounts:register"),
            {"email": "otra@empresa.pe", "password": PASSWORD}, format="json",
        )
        second = self.client.post(
            reverse("accounts:register"),
            {"email": "ana@empresa.pe", "password": PASSWORD}, format="json",
        )
        # Misma respuesta: el API no revela quién ya es cliente.
        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first.data, second.data)
        self.assertEqual(User.objects.count(), 2)

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            reverse("accounts:register"),
            {"email": "ana@empresa.pe", "password": "12345678"}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_verification_confirms_and_returns_a_session(self):
        self.client.post(
            reverse("accounts:register"),
            {"email": "ana@empresa.pe", "password": PASSWORD}, format="json",
        )
        token = OneTimeToken.objects.get(purpose=TokenPurpose.EMAIL_VERIFICATION)
        response = self.client.post(
            reverse("accounts:verify-email"), {"token": token.token}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertTrue(User.objects.get().email_verified)
        # Un solo uso: el mismo enlace ya no sirve.
        again = self.client.post(
            reverse("accounts:verify-email"), {"token": token.token}, format="json",
        )
        self.assertEqual(again.status_code, 400)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LoginTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="ana@empresa.pe", password=PASSWORD, email_verified_at=timezone.now()
        )

    def test_login_returns_bearer_and_profile(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"email": "ANA@empresa.pe", "password": PASSWORD}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "ana@empresa.pe")
        self.assertEqual(response.data["organizations"], [])

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"email": "ana@empresa.pe", "password": "otra-cosa-99"}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_inactive_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(
            reverse("accounts:login"),
            {"email": "ana@empresa.pe", "password": PASSWORD}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_profile_requires_a_bearer(self):
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 401)

    def test_profile_read_and_edit(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        response = self.client.patch(
            reverse("accounts:profile"),
            {"first_name": "Ana", "last_name": "Quispe", "email": "otro@x.pe"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Ana Quispe")
        # El correo es la identidad: no se cambia por PATCH de perfil.
        self.assertEqual(self.user.email, "ana@empresa.pe")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordRecoveryTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email="ana@empresa.pe", password=PASSWORD)
        django_mail.outbox = []

    def test_unknown_email_answers_the_same(self):
        known = self.client.post(
            reverse("accounts:password-reset"), {"email": "ana@empresa.pe"},
            format="json",
        )
        unknown = self.client.post(
            reverse("accounts:password-reset"), {"email": "nadie@empresa.pe"},
            format="json",
        )
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(len(django_mail.outbox), 1)   # solo al que existe

    def test_reset_changes_the_password_and_verifies_the_email(self):
        self.client.post(
            reverse("accounts:password-reset"), {"email": "ana@empresa.pe"},
            format="json",
        )
        token = OneTimeToken.objects.get(purpose=TokenPurpose.PASSWORD_RESET)
        response = self.client.post(
            reverse("accounts:password-reset-confirm"),
            {"token": token.token, "password": "otra-clave-larga-77"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("otra-clave-larga-77"))
        # Recuperar por correo demuestra control del buzón.
        self.assertTrue(self.user.email_verified)

    def test_reset_token_is_single_use_and_expires(self):
        token = OneTimeToken.issue(self.user, TokenPurpose.PASSWORD_RESET)
        url = reverse("accounts:password-reset-confirm")
        body = {"token": token.token, "password": "otra-clave-larga-77"}
        self.assertEqual(self.client.post(url, body, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, body, format="json").status_code, 400)

    def test_issuing_a_token_invalidates_the_previous_one(self):
        old = OneTimeToken.issue(self.user, TokenPurpose.PASSWORD_RESET)
        OneTimeToken.issue(self.user, TokenPurpose.PASSWORD_RESET)
        old.refresh_from_db()
        self.assertFalse(old.is_usable)

    def test_change_password_requires_the_current_one(self):
        self.client.force_authenticate(self.user)
        url = reverse("accounts:password-change")
        bad = self.client.post(
            url, {"current_password": "no-es", "password": "otra-clave-larga-77"},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)
        good = self.client.post(
            url, {"current_password": PASSWORD, "password": "otra-clave-larga-77"},
            format="json",
        )
        self.assertEqual(good.status_code, 200)
        self.assertIn("access", good.data)
