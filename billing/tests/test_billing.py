"""Prueba gratuita, puerta de pago, pagos y referidos."""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Membership, Organization, Role, User
from accounts.tests.test_tenancy import make_org, make_user
from billing import services
from billing.models import Payment, PaymentStatus, Plan, Referral, ReferralReward, Subscription


def expirar(org: Organization) -> None:
    sub = services.ensure_subscription(org)
    sub.trial_end = timezone.now() - datetime.timedelta(days=1)
    sub.current_period_end = None
    sub.save()


@override_settings(BILLING_GATEWAY="fake", DEBUG=True)
class PruebaYPuertaTests(APITestCase):
    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_la_empresa_nueva_nace_en_prueba_de_7_dias(self):
        sub = self.org.subscription
        self.assertEqual(sub.status, "trialing")
        self.assertTrue(sub.is_active)
        self.assertIn(sub.days_left, (7, 8))
        # y la sesión lo cuenta
        me = self.client.get(reverse("accounts:profile")).data
        self.assertEqual(me["organizations"][0]["subscription"]["status"], "trialing")

    def test_terminada_la_prueba_los_datos_responden_402_pero_pagar_sigue_abierto(self):
        self.assertEqual(self.client.get(reverse("sync:status")).status_code, 200)
        expirar(self.org)
        r = self.client.get(reverse("sync:status"))
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.data["code"], "suscripcion_vencida")
        self.assertEqual(self.client.get(reverse("billing:subscription")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_pagar_con_la_pasarela_de_prueba_activa_el_plan_sin_quemar_la_prueba(self):
        trial_end = self.org.subscription.trial_end
        r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["payment"]["status"], "approved")
        sub = Subscription.objects.get(organization=self.org)
        self.assertEqual(sub.status, "active")
        self.assertEqual(sub.plan.code, "mensual")
        # el periodo empieza cuando termina la prueba
        self.assertAlmostEqual(
            (sub.current_period_end - trial_end).total_seconds(), 30 * 86400, delta=5,
        )
        # y vuelve a abrir los datos
        expirar_prueba = sub.trial_end - datetime.timedelta(days=10)
        sub.trial_end = expirar_prueba; sub.save()
        self.assertEqual(self.client.get(reverse("sync:status")).status_code, 200)

    def test_el_anual_da_doce_meses_y_declara_su_ahorro(self):
        planes = self.client.get(reverse("billing:plans")).data["plans"]
        anual = next(p for p in planes if p["code"] == "anual")
        self.assertEqual(anual["months"], 12)
        self.assertEqual(anual["savings_pct"], 17)
        self.client.post(reverse("billing:checkout"), {"plan": "anual"}, format="json")
        sub = Subscription.objects.get(organization=self.org)
        self.assertGreater(sub.current_period_end, timezone.now() + datetime.timedelta(days=360))

    def test_solo_lectura_no_puede_pagar(self):
        lectura = make_user("lectura@uno.pe")
        Membership.objects.create(user=lectura, organization=self.org, role=Role.VIEWER)
        self.client.force_authenticate(lectura)
        self.assertEqual(self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json").status_code, 403)

    def test_plan_inexistente(self):
        r = self.client.post(reverse("billing:checkout"), {"plan": "oro"}, format="json")
        self.assertEqual(r.status_code, 400)


@override_settings(BILLING_GATEWAY="fake", DEBUG=True, FRONTEND_URL="https://app.empresario.pe")
class ReferidosTests(APITestCase):
    def setUp(self):
        self.ref = make_user("referente@uno.pe")
        self.org_ref = make_org("20100000001", self.ref)

    def test_al_registrarse_con_codigo_queda_anotado(self):
        r = self.client.post(reverse("accounts:register"), {
            "email": "nuevo@dos.pe", "password": "clave-larga-segura-99",
            "referral_code": self.ref.referral_code.lower(),
        }, format="json")
        self.assertEqual(r.status_code, 202)
        nuevo = User.objects.get(email="nuevo@dos.pe")
        self.assertEqual(nuevo.referred_by, self.ref)
        self.assertTrue(Referral.objects.filter(referrer=self.ref, referred=nuevo).exists())

    def test_un_codigo_desconocido_no_bloquea_el_registro(self):
        r = self.client.post(reverse("accounts:register"), {
            "email": "nuevo@dos.pe", "password": "clave-larga-segura-99", "referral_code": "NOEXISTE",
        }, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertIsNone(User.objects.get(email="nuevo@dos.pe").referred_by)

    def test_todo_usuario_tiene_codigo_y_enlace(self):
        self.client.force_authenticate(self.ref)
        r = self.client.get(reverse("billing:referrals"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["code"]), 8)
        self.assertEqual(r.data["link"], f"https://app.empresario.pe/registro?ref={self.ref.referral_code}")
        self.assertEqual(r.data["target"], 5)

    def test_cinco_referidos_que_pagan_dan_un_mes_gratis(self):
        antes = self.org_ref.subscription.access_until
        for i in range(5):
            u = make_user(f"ref{i}@x.pe"); services.link_referral(u, self.ref.referral_code)
            org = make_org(f"2020000000{i}", u)
            self.client.force_authenticate(u)
            self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
            if i < 4:
                self.assertEqual(ReferralReward.objects.filter(user=self.ref).count(), 0, f"tras {i+1}")
        reward = ReferralReward.objects.get(user=self.ref)
        self.assertEqual(reward.days, 30)
        self.assertEqual(reward.applied_to.organization, self.org_ref)
        sub = Subscription.objects.get(organization=self.org_ref)
        self.assertAlmostEqual((sub.access_until - antes).total_seconds(), 30 * 86400, delta=5)
        self.assertEqual(sub.bonus_days, 30)
        # el resumen lo cuenta
        self.client.force_authenticate(self.ref)
        data = self.client.get(reverse("billing:referrals")).data
        self.assertEqual(data["converted"], 5)
        self.assertEqual(data["progress"], 0)
        self.assertEqual(len(data["rewards"]), 1)

    def test_un_mismo_referido_cuenta_una_sola_vez_aunque_pague_dos(self):
        u = make_user("ref@x.pe"); services.link_referral(u, self.ref.referral_code)
        make_org("20200000001", u)
        self.client.force_authenticate(u)
        self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(Referral.objects.filter(referrer=self.ref, converted_at__isnull=False).count(), 1)

    def test_el_premio_espera_a_que_el_referente_tenga_empresa(self):
        sin_empresa = make_user("sin@empresa.pe")
        for i in range(5):
            u = make_user(f"r{i}@x.pe"); services.link_referral(u, sin_empresa.referral_code)
            make_org(f"2030000000{i}", u)
            self.client.force_authenticate(u)
            self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        reward = ReferralReward.objects.get(user=sin_empresa)
        self.assertIsNone(reward.applied_at)
        # crea su empresa → se aplica
        sin_empresa.email_verified_at = timezone.now(); sin_empresa.save()
        self.client.force_authenticate(sin_empresa)
        r = self.client.post(reverse("accounts:organizations"), {"ruc": "20604442533"}, format="json")
        self.assertEqual(r.status_code, 201)
        reward.refresh_from_db()
        self.assertIsNotNone(reward.applied_at)
        self.assertEqual(Subscription.objects.get(organization__ruc="20604442533").bonus_days, 30)


@override_settings(BILLING_GATEWAY="mercadopago", MERCADOPAGO_ACCESS_TOKEN="TEST-token", API_PUBLIC_URL="https://api.empresario.pe", FRONTEND_URL="https://app.empresario.pe", MERCADOPAGO_TEST_PAYER_EMAIL="")
class MercadoPagoTests(APITestCase):
    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_checkout_crea_preferencia_y_devuelve_url(self):
        # Plan de pago único: va por Checkout Pro (preferencia), no por suscripción.
        Plan.objects.filter(code="mensual").update(recurring=False)
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "pref-1", "init_point": "https://www.mercadopago.com.pe/checkout/v1/redirect?pref_id=pref-1"}
        with patch("billing.gateways.requests.post", return_value=Resp()) as post:
            r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["checkout_url"].startswith("https://www.mercadopago.com.pe/"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["items"][0]["unit_price"], 99.9)
        self.assertEqual(body["notification_url"], "https://api.empresario.pe/api/billing/webhook/mercadopago/")
        self.assertEqual(r.data["payment"]["status"], "pending")

    @override_settings(API_PUBLIC_URL="")
    def test_sin_api_public_url_las_urls_salen_del_origen_de_la_peticion(self):
        Plan.objects.filter(code="mensual").update(recurring=False)
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "pref-2", "init_point": "https://mp/x"}
        with patch("billing.gateways.requests.post", return_value=Resp()) as post:
            self.client.post(
                reverse("billing:checkout"), {"plan": "mensual"}, format="json",
                HTTP_ORIGIN="https://mi-tunel.trycloudflare.com",
            )
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["notification_url"], "https://mi-tunel.trycloudflare.com/api/billing/webhook/mercadopago/")
        self.assertEqual(body["back_urls"]["success"], "https://mi-tunel.trycloudflare.com/suscripcion?estado=ok")

    def test_el_webhook_confirma_consultando_el_pago_y_activa(self):
        pago = Payment.objects.create(
            subscription=self.org.subscription, plan=Plan.objects.get(code="mensual"),
            amount=99.90, gateway="mercadopago", created_by=self.ana,
        )
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": 777, "status": "approved", "external_reference": str(pago.pk)}
        self.client.force_authenticate(None)
        with patch("billing.gateways.requests.get", return_value=Resp()):
            r = self.client.post(reverse("billing:mp-webhook") + "?type=payment&data.id=777", {}, format="json")
        self.assertEqual(r.status_code, 200)
        pago.refresh_from_db()
        self.assertEqual(pago.status, PaymentStatus.APPROVED)
        self.assertEqual(pago.gateway_payment_id, "777")
        self.assertEqual(Subscription.objects.get(organization=self.org).status, "active")
        # repetido: idempotente
        with patch("billing.gateways.requests.get", return_value=Resp()):
            self.client.post(reverse("billing:mp-webhook") + "?type=payment&data.id=777", {}, format="json")
        self.assertEqual(Payment.objects.filter(status=PaymentStatus.APPROVED).count(), 1)


@override_settings(BILLING_GATEWAY="fake", DEBUG=True)
class RecurrenteFakeTests(APITestCase):
    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_el_mensual_queda_con_renovacion_automatica(self):
        r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["payment"]["kind"], "recurring_setup")
        sub = Subscription.objects.get(organization=self.org)
        self.assertTrue(sub.auto_renew)
        self.assertEqual(sub.gateway_status, "authorized")
        self.assertEqual(sub.next_charge_at, sub.current_period_end)
        d = self.client.get(reverse("billing:subscription")).data
        self.assertTrue(d["auto_renew"]); self.assertTrue(d["supports_recurring"])

    def test_cancelar_deja_de_renovar_pero_no_corta_lo_pagado(self):
        self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        fin = Subscription.objects.get(organization=self.org).current_period_end
        r = self.client.post(reverse("billing:cancel"))
        self.assertEqual(r.status_code, 200)
        sub = Subscription.objects.get(organization=self.org)
        self.assertFalse(sub.auto_renew)
        self.assertEqual(sub.current_period_end, fin)
        self.assertTrue(sub.is_active)
        self.assertEqual(self.client.get(reverse("sync:status")).status_code, 200)


@override_settings(BILLING_GATEWAY="mercadopago", MERCADOPAGO_ACCESS_TOKEN="TEST-token", API_PUBLIC_URL="https://api.empresario.pe", FRONTEND_URL="https://app.empresario.pe", MERCADOPAGO_TEST_PAYER_EMAIL="")
class MercadoPagoRecurrenteTests(APITestCase):
    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_checkout_recurrente_crea_preapproval_y_devuelve_init_point(self):
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "pre-1", "status": "pending", "init_point": "https://www.mercadopago.com.pe/subscriptions/checkout?preapproval_id=pre-1"}
        with patch("billing.gateways.requests.post", return_value=Resp()) as post:
            r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIn("/subscriptions/", r.data["checkout_url"])
        self.assertTrue(post.call_args.args[0].endswith("/preapproval"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["auto_recurring"]["frequency"], 1)
        self.assertEqual(body["auto_recurring"]["transaction_amount"], 99.9)
        self.assertEqual(body["payer_email"], "ana@uno.pe")
        sub = Subscription.objects.get(organization=self.org)
        self.assertEqual(body["external_reference"], str(sub.pk))
        self.assertEqual(sub.gateway_subscription_id, "pre-1")
        self.assertFalse(sub.auto_renew)  # aún no autorizada
        self.assertEqual(sub.status, "trialing")

    def test_el_anual_cobra_cada_doce_meses(self):
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "pre-2", "status": "pending", "init_point": "https://mp/x"}
        with patch("billing.gateways.requests.post", return_value=Resp()) as post:
            self.client.post(reverse("billing:checkout"), {"plan": "anual"}, format="json")
        self.assertEqual(post.call_args.kwargs["json"]["auto_recurring"]["frequency"], 12)

    def _con_preapproval(self):
        sub = self.org.subscription
        sub.gateway = "mercadopago"; sub.gateway_subscription_id = "pre-1"; sub.gateway_status = "pending"; sub.save()
        setup = Payment.objects.create(subscription=sub, plan=Plan.objects.get(code="mensual"), amount=99.90,
                                       gateway="mercadopago", kind="recurring_setup", created_by=self.ana)
        return sub, setup

    def test_webhook_de_autorizacion_y_primer_cobro_activan(self):
        sub, setup = self._con_preapproval()
        self.client.force_authenticate(None)
        class Pre:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "pre-1", "status": "authorized", "external_reference": str(sub.pk), "next_payment_date": "2026-09-23T12:00:00.000-04:00"}
        with patch("billing.gateways.requests.get", return_value=Pre()):
            self.client.post(reverse("billing:mp-webhook") + "?type=subscription_preapproval&data.id=pre-1", {}, format="json")
        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew); self.assertEqual(sub.gateway_status, "authorized")
        self.assertIsNotNone(sub.next_charge_at)
        # primer cobro: llega como pago con external_reference = suscripción
        class Pay:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": 9001, "status": "approved", "external_reference": str(sub.pk), "transaction_amount": 99.9, "currency_id": "PEN", "metadata": {"preapproval_id": "pre-1"}}
        with patch("billing.gateways.requests.get", return_value=Pay()):
            self.client.post(reverse("billing:mp-webhook") + "?type=payment&data.id=9001", {}, format="json")
        setup.refresh_from_db(); sub.refresh_from_db()
        self.assertEqual(setup.status, PaymentStatus.APPROVED)
        self.assertEqual(setup.gateway_payment_id, "9001")
        self.assertEqual(sub.status, "active")
        fin1 = sub.current_period_end
        # segundo cobro (mes siguiente): nuevo pago y periodo extendido
        class Pay2:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "ap-1", "preapproval_id": "pre-1", "status": "processed", "transaction_amount": 99.9, "currency_id": "PEN", "payment": {"id": 9002, "status": "approved"}}
        with patch("billing.gateways.requests.get", return_value=Pay2()):
            self.client.post(reverse("billing:mp-webhook") + "?type=subscription_authorized_payment&data.id=ap-1", {}, format="json")
            # repetido: idempotente
            self.client.post(reverse("billing:mp-webhook") + "?type=subscription_authorized_payment&data.id=ap-1", {}, format="json")
        sub.refresh_from_db()
        self.assertEqual(Payment.objects.filter(subscription=sub, status=PaymentStatus.APPROVED).count(), 2)
        self.assertAlmostEqual((sub.current_period_end - fin1).total_seconds(), 30 * 86400, delta=5)
        self.assertEqual(Payment.objects.get(gateway_payment_id="9002").kind, "recurring_charge")

    def test_cancelar_avisa_a_mercado_pago(self):
        sub, _ = self._con_preapproval()
        sub.auto_renew = True; sub.save()
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"id": "pre-1", "status": "cancelled"}
        with patch("billing.gateways.requests.put", return_value=Resp()) as put:
            r = self.client.post(reverse("billing:cancel"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(put.call_args.args[0].endswith("/preapproval/pre-1"))
        self.assertEqual(put.call_args.kwargs["json"], {"status": "cancelled"})
        sub.refresh_from_db(); self.assertFalse(sub.auto_renew)


@override_settings(BILLING_GATEWAY="fake", DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CorreosTests(APITestCase):
    def setUp(self):
        from django.core import mail
        mail.outbox.clear()
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_el_pago_aprobado_manda_recibo_en_html_y_texto(self):
        from django.core import mail
        self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        recibo = next(m for m in mail.outbox if "Pago recibido" in m.subject)
        self.assertEqual(recibo.to, ["ana@uno.pe"])
        self.assertIn("S/ 99.90", recibo.body)                     # texto plano
        html = recibo.alternatives[0][0]
        self.assertIn("text/html", recibo.alternatives[0][1])
        self.assertIn("Pago recibido", html); self.assertIn("20100000001", html); self.assertIn("/suscripcion", html)

    def test_cancelar_la_renovacion_avisa_por_correo(self):
        from django.core import mail
        self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        mail.outbox.clear()
        self.client.post(reverse("billing:cancel"))
        self.assertTrue(any("cancelada" in m.subject for m in mail.outbox))

    def test_el_aviso_de_fin_de_prueba_sale_una_vez_y_solo_a_quien_decide(self):
        from django.core import mail
        from billing.tasks import avisar_fin_de_prueba
        lectura = make_user("lectura@uno.pe")
        Membership.objects.create(user=lectura, organization=self.org, role=Role.VIEWER)
        sub = self.org.subscription
        sub.trial_end = timezone.now() + datetime.timedelta(days=1, hours=2); sub.save()
        self.assertEqual(avisar_fin_de_prueba(), 1)
        self.assertEqual(mail.outbox[-1].to, ["ana@uno.pe"])
        self.assertIn("termina", mail.outbox[-1].subject)
        self.assertIn("Elegir mi plan", mail.outbox[-1].alternatives[0][0])
        # segunda pasada: ya avisado
        self.assertEqual(avisar_fin_de_prueba(), 0)
        # una con plan pagado no recibe aviso
        otra = make_org("20100000002", self.ana); s2 = otra.subscription
        s2.trial_end = timezone.now() + datetime.timedelta(days=1); s2.current_period_end = timezone.now() + datetime.timedelta(days=40); s2.save()
        self.assertEqual(avisar_fin_de_prueba(), 0)

    def test_la_verificacion_de_cuenta_va_en_html_con_el_enlace_visible(self):
        from django.core import mail
        from accounts.services import mail as amail
        amail.send_verification(self.ana)
        m = mail.outbox[-1]
        self.assertIn("/verificar-correo?token=", m.body)
        html = m.alternatives[0][0]
        self.assertIn("Confirmar mi correo", html); self.assertIn("/verificar-correo?token=", html)


@override_settings(BILLING_GATEWAY="", DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SinPasarelaTests(APITestCase):
    """Sin credenciales de cobro no se puede pagar, ni en desarrollo.

    Caso real: con DEBUG y sin token, `fake` se activaba sola; un clic en
    «Continuar» dejaba el plan activo y mandaba el correo de bienvenida sin
    que nadie hubiera levantado una pasarela.
    """

    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_el_checkout_se_rechaza_sin_crear_pago_ni_correo(self):
        from django.core import mail
        from billing.models import Payment

        r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")

        self.assertEqual(r.status_code, 503, r.data)
        self.assertIn("no están habilitados", r.data["detail"])
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        sub = Subscription.objects.get(organization=self.org)
        self.assertEqual(sub.status, "trialing")

    def test_la_pantalla_sabe_que_no_hay_pasarela(self):
        r = self.client.get(reverse("billing:subscription"))
        self.assertEqual(r.data["gateway"], "none")

    @override_settings(BILLING_GATEWAY="fake", DEBUG=False)
    def test_fake_fuera_de_debug_no_existe(self):
        r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 503)

    @override_settings(BILLING_GATEWAY="mercadopago", MERCADOPAGO_ACCESS_TOKEN="", MERCADOPAGO_TEST_PAYER_EMAIL="")
    def test_mercadopago_sin_token_no_procede(self):
        r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 503)


@override_settings(BILLING_GATEWAY="mercadopago", MERCADOPAGO_ACCESS_TOKEN="TEST-token", FRONTEND_URL="http://localhost:3000", MERCADOPAGO_TEST_PAYER_EMAIL="")
class MercadoPagoDesdeLocalhostTests(APITestCase):
    """Mercado Pago rechaza URLs de retorno que no sean públicas. Se explica
    antes de llamar, en vez de devolver un 502 con «400 Bad Request»."""

    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def test_explica_que_hace_falta_el_tunel(self):
        from billing.models import Payment

        r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 503, r.data)
        self.assertIn("túnel", r.data["detail"])
        self.assertEqual(Payment.objects.count(), 1)  # el pago queda como rastro, sin aprobar
        self.assertEqual(Payment.objects.get().status, "pending")


@override_settings(
    BILLING_GATEWAY="mercadopago", MERCADOPAGO_ACCESS_TOKEN="APP_USR-vendedor-de-prueba",
    FRONTEND_URL="https://app.empresario.pe", MERCADOPAGO_TEST_PAYER_EMAIL="",
)
class VendedorDePruebaTests(APITestCase):
    """Con un vendedor de prueba, el pagador debe ser un comprador de prueba.

    Caso real: «Both payer and collector must be real or test users» al mandar
    el correo del usuario de Empresario con el token de un test_user.
    """

    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)
        from billing import gateways
        gateways._COLLECTOR_ES_DE_PRUEBA.clear()

    class Me:
        status_code = 200
        def json(self): return {"id": 1, "tags": ["test_user", "user_product_seller"]}

    def test_sin_correo_de_comprador_de_prueba_se_explica(self):
        with patch("billing.gateways.requests.get", return_value=self.Me()):
            r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 503, r.data)
        self.assertIn("MERCADOPAGO_TEST_PAYER_EMAIL", r.data["detail"])

    @override_settings(MERCADOPAGO_TEST_PAYER_EMAIL="test_user_9@testuser.com")
    def test_con_correo_de_comprador_de_prueba_se_usa_ese(self):
        class Resp:
            status_code = 200
            def json(self): return {"id": "pre-9", "status": "pending", "init_point": "https://www.mercadopago.com.pe/subscriptions/checkout?preapproval_id=pre-9"}
        with patch("billing.gateways.requests.get", return_value=self.Me()), \
                patch("billing.gateways.requests.post", return_value=Resp()) as post:
            r = self.client.post(reverse("billing:checkout"), {"plan": "mensual"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(post.call_args.kwargs["json"]["payer_email"], "test_user_9@testuser.com")


class NormalizarCorreoDePruebaTests(APITestCase):
    def test_usuario_o_comillas_se_convierten_al_correo(self):
        from billing.gateways import _normalizar_correo_de_prueba as n

        self.assertEqual(n('"TESTUSER9054147675332198239"'), "test_user_9054147675332198239@testuser.com")
        self.assertEqual(n("testuser12"), "test_user_12@testuser.com")
        self.assertEqual(n(" test_user_1@testuser.com "), "test_user_1@testuser.com")
        self.assertEqual(n(""), "")
