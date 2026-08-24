from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from leads.models import Lead


class LeadCreateTests(APITestCase):
    def test_anyone_can_leave_their_details(self):
        response = self.client.post(reverse("leads:create"), {
            "name": "  Rosa Quispe ", "email": "Rosa@Empresa.PE",
            "phone": "+51 999 888 777", "ruc": "20604442533",
            "company": "Empresa SAC", "message": "Quiero ver la demo",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        lead = Lead.objects.get()
        self.assertEqual(lead.name, "Rosa Quispe")
        self.assertEqual(lead.email, "rosa@empresa.pe")
        self.assertEqual(lead.source, "landing")

    def test_email_and_name_are_required(self):
        response = self.client.post(reverse("leads:create"), {"name": "X"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)
        self.assertIn("name", response.data)
        self.assertFalse(Lead.objects.exists())

    def test_bad_ruc_is_rejected(self):
        response = self.client.post(reverse("leads:create"), {
            "name": "Rosa", "email": "rosa@empresa.pe", "ruc": "123",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("ruc", response.data)

    @override_settings(LEADS_NOTIFY_EMAIL="ventas@empresario.pe",
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_team_is_notified_when_configured(self):
        self.client.post(reverse("leads:create"), {
            "name": "Rosa", "email": "rosa@empresa.pe",
        }, format="json")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("rosa@empresa.pe", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["ventas@empresario.pe"])
