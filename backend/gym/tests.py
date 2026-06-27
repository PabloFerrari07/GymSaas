import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests as requests_lib
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from gym.models import (
    Member,
    NotificationLog,
    Payment,
    SubscriptionPlan,
    Tenant,
    WhatsAppSession,
)
from gym.services.mercadopago_client import create_payment_preference, get_payment_info
from gym.services.whatsapp_client import send_whatsapp_message
from gym.tasks import check_subscriptions

WEBHOOK_URL = "/api/webhooks/whatsapp/"
TEST_TOKEN = "test-token-secreto"


class MemberDueDateTest(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="dueño_test", password="test1234")
        self.tenant = Tenant.objects.create(name="Gym de prueba", owner=owner)
        self.plan = SubscriptionPlan.objects.create(
            tenant=self.tenant,
            name="Mensual",
            duration_days=30,
            price=10000,
        )

    def test_due_date_se_calcula_solo(self):
        member = Member.objects.create(
            tenant=self.tenant,
            first_name="Juan",
            last_name="Pérez",
            phone="11111111",
            current_plan=self.plan,
            start_date=date(2026, 1, 1),
        )
        self.assertEqual(member.due_date, date(2026, 1, 31))


@override_settings(WHATSAPP_SHARED_TOKEN=TEST_TOKEN)
class WhatsAppWebhookTest(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="admin_gym", password="test1234")
        self.tenant = Tenant.objects.create(name="Gym Test", owner=owner)
        self.session = WhatsAppSession.objects.create(tenant=self.tenant)

    def _post(self, payload, token=TEST_TOKEN):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_INTERNAL_TOKEN=token,
        )

    def test_token_invalido_devuelve_401(self):
        resp = self._post(
            {"tenant_id": str(self.tenant.id), "event": "session_connected"},
            token="token-incorrecto",
        )
        self.assertEqual(resp.status_code, 401)

    def test_sin_token_devuelve_401(self):
        resp = self.client.post(
            WEBHOOK_URL,
            data=json.dumps(
                {"tenant_id": str(self.tenant.id), "event": "session_connected"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_session_connected_actualiza_estado(self):
        self.assertEqual(self.session.status, WhatsAppSession.STATUS_PENDING_QR)
        resp = self._post(
            {"tenant_id": str(self.tenant.id), "event": "session_connected"}
        )
        self.assertEqual(resp.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WhatsAppSession.STATUS_CONNECTED)

    def test_session_disconnected_actualiza_estado(self):
        self.session.status = WhatsAppSession.STATUS_CONNECTED
        self.session.save()
        resp = self._post(
            {"tenant_id": str(self.tenant.id), "event": "session_disconnected"}
        )
        self.assertEqual(resp.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WhatsAppSession.STATUS_DISCONNECTED)

    def test_message_delivered_marca_notification_log(self):
        plan = SubscriptionPlan.objects.create(
            tenant=self.tenant, name="Mensual", duration_days=30, price=5000
        )
        member = Member.objects.create(
            tenant=self.tenant,
            first_name="Ana",
            last_name="García",
            phone="22222222",
            current_plan=plan,
            start_date=date(2026, 1, 1),
        )
        log = NotificationLog.objects.create(
            member=member,
            type=NotificationLog.TYPE_DUE_SOON,
            whatsapp_message_id="WA-MSG-ID-001",
        )
        resp = self._post(
            {
                "tenant_id": str(self.tenant.id),
                "event": "message_delivered",
                "data": {"whatsapp_message_id": "WA-MSG-ID-001", "status": 4},
            }
        )
        self.assertEqual(resp.status_code, 200)
        log.refresh_from_db()
        self.assertTrue(log.delivered)

    def test_message_sent_puebla_whatsapp_message_id(self):
        plan = SubscriptionPlan.objects.create(
            tenant=self.tenant, name="Mensual", duration_days=30, price=5000
        )
        member = Member.objects.create(
            tenant=self.tenant,
            first_name="Carlos",
            last_name="López",
            phone="33333333",
            current_plan=plan,
            start_date=date(2026, 1, 1),
        )
        log = NotificationLog.objects.create(
            member=member,
            type=NotificationLog.TYPE_DUE_SOON,
            job_id="job-abc123",
        )
        self.assertEqual(log.whatsapp_message_id, "")

        resp = self._post(
            {
                "tenant_id": str(self.tenant.id),
                "event": "message_sent",
                "data": {"job_id": "job-abc123", "whatsapp_message_id": "WA-SENT-999"},
            }
        )
        self.assertEqual(resp.status_code, 200)
        log.refresh_from_db()
        self.assertEqual(log.whatsapp_message_id, "WA-SENT-999")
        # delivered aún debe ser False — message_sent no lo activa
        self.assertFalse(log.delivered)

    def test_evento_desconocido_devuelve_200(self):
        resp = self._post({"tenant_id": str(self.tenant.id), "event": "future_event"})
        self.assertEqual(resp.status_code, 200)

    def test_payload_invalido_devuelve_400(self):
        resp = self.client.post(
            WEBHOOK_URL,
            data="esto no es json{{{",
            content_type="application/json",
            HTTP_X_INTERNAL_TOKEN=TEST_TOKEN,
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(
    WHATSAPP_SERVICE_URL="http://whatsapp:3000",
    WHATSAPP_SHARED_TOKEN="test-token",
)
class SendWhatsAppMessageTest(TestCase):
    """
    Verifica el contrato de send_whatsapp_message():
    - job_id viaja al nivel raíz del resultado exitoso
    - errores de red devuelven ok=False sin lanzar excepción
    """

    _NODE_RESPONSE = {"status": "queued", "job_id": "1-1749000000000-abc123"}

    def _mock_post(self, json_data=None, exc=None):
        if exc:
            return patch("gym.services.whatsapp_client.requests.post", side_effect=exc)
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data or self._NODE_RESPONSE
        mock_resp.raise_for_status.return_value = None
        return patch(
            "gym.services.whatsapp_client.requests.post", return_value=mock_resp
        )

    def test_retorna_ok_y_job_id_en_nivel_raiz(self):
        with self._mock_post() as mock_post:
            result = send_whatsapp_message("1", "5491112345678", "Hola")

        self.assertTrue(result["ok"])
        self.assertEqual(result["job_id"], "1-1749000000000-abc123")
        self.assertIn("data", result)
        mock_post.assert_called_once_with(
            "http://whatsapp:3000/send-message",
            json={"tenant_id": "1", "phone": "5491112345678", "message": "Hola"},
            headers={"X-Internal-Token": "test-token"},
            timeout=5,
        )

    def test_connection_error_devuelve_ok_false_sin_excepcion(self):
        with self._mock_post(exc=requests_lib.exceptions.ConnectionError()):
            result = send_whatsapp_message("1", "5491112345678", "Hola")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "connection_error")
        self.assertNotIn("job_id", result)

    def test_timeout_devuelve_ok_false_sin_excepcion(self):
        with self._mock_post(exc=requests_lib.exceptions.Timeout()):
            result = send_whatsapp_message("1", "5491112345678", "Hola")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "timeout")


class CheckSubscriptionsTest(TestCase):
    """
    Tests para el job check_subscriptions.
    send_whatsapp_message se mockea para no depender del servicio Node.
    """

    def setUp(self):
        owner = User.objects.create_user(username="owner_gym", password="pass")
        self.tenant = Tenant.objects.create(
            name="Gym Test",
            owner=owner,
            admin_phone="5491100000000",
        )
        self.plan = SubscriptionPlan.objects.create(
            tenant=self.tenant,
            name="Mensual",
            duration_days=30,
            price=5000,
        )

    def _make_member(self, due_date, phone="5491199999999", first_name="Juan"):
        return Member.objects.create(
            tenant=self.tenant,
            first_name=first_name,
            last_name="Perez",
            phone=phone,
            current_plan=self.plan,
            start_date=date(2026, 1, 1),
            due_date=due_date,
        )

    @patch(
        "gym.tasks.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-test-1", "data": {}},
    )
    def test_vencimiento_hoy_genera_2_envios_y_2_logs(self, mock_send):
        today = timezone.localdate()
        member = self._make_member(due_date=today)

        check_subscriptions()

        self.assertEqual(mock_send.call_count, 2)
        logs = NotificationLog.objects.filter(
            member=member, type=NotificationLog.TYPE_DUE_TODAY
        )
        self.assertEqual(logs.count(), 2)
        # Ambos logs tienen el job_id correcto
        self.assertTrue(all(log.job_id == "job-test-1" for log in logs))

    @patch(
        "gym.tasks.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-test-2", "data": {}},
    )
    def test_vencimiento_otro_dia_no_genera_envios(self, mock_send):
        self._make_member(due_date=date(2025, 6, 1))

        check_subscriptions()

        mock_send.assert_not_called()
        self.assertEqual(NotificationLog.objects.count(), 0)

    @patch(
        "gym.tasks.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-test-3", "data": {}},
    )
    def test_ya_notificado_hoy_no_duplica(self, mock_send):
        today = timezone.localdate()
        member = self._make_member(due_date=today)
        # Simular notificacion exitosa previa del mismo dia (job_id no vacio)
        NotificationLog.objects.create(
            member=member,
            type=NotificationLog.TYPE_DUE_TODAY,
            job_id="job-previo-exitoso",
        )

        check_subscriptions()

        mock_send.assert_not_called()

    @patch(
        "gym.tasks.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-reintento", "data": {}},
    )
    def test_log_fallido_hoy_permite_reintento(self, mock_send):
        today = timezone.localdate()
        member = self._make_member(due_date=today)
        # Log de hoy con job_id vacio = envio fallido → no debe bloquear el reintento
        NotificationLog.objects.create(
            member=member,
            type=NotificationLog.TYPE_DUE_TODAY,
            job_id="",
        )

        check_subscriptions()

        # El job debe intentar notificar de nuevo (socio + admin)
        self.assertEqual(mock_send.call_count, 2)

    @patch(
        "gym.tasks.send_whatsapp_message",
        return_value={"ok": False, "error": "connection_error"},
    )
    def test_fallo_whatsapp_no_crashea_y_crea_logs(self, mock_send):
        today = timezone.localdate()
        member = self._make_member(due_date=today)

        # No debe lanzar excepcion
        check_subscriptions()

        # Se llama 2 veces (socio + admin), falla las 2, pero los logs igual se crean
        self.assertEqual(mock_send.call_count, 2)
        logs = NotificationLog.objects.filter(
            member=member, type=NotificationLog.TYPE_DUE_TODAY
        )
        self.assertEqual(logs.count(), 2)
        self.assertTrue(all(log.job_id == "" for log in logs))

    @patch(
        "gym.tasks.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-test-5", "data": {}},
    )
    def test_sin_admin_phone_solo_1_envio_al_socio(self, mock_send):
        today = timezone.localdate()
        self.tenant.admin_phone = ""
        self.tenant.save()
        member = self._make_member(due_date=today)

        check_subscriptions()

        # Solo se notifica al socio, no al admin
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(NotificationLog.objects.filter(member=member).count(), 1)


@override_settings(MERCADOPAGO_ACCESS_TOKEN="TEST-fake-token")
class MercadoPagoClientTest(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="mp_owner", password="pass")
        self.tenant = Tenant.objects.create(name="Gym MP", owner=owner)
        self.plan = SubscriptionPlan.objects.create(
            tenant=self.tenant,
            name="Mensual",
            duration_days=30,
            price=Decimal("5000.00"),
        )
        self.member = Member.objects.create(
            tenant=self.tenant,
            first_name="Ana",
            last_name="Lopez",
            phone="5491188888888",
            current_plan=self.plan,
            start_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )

    def _make_mock_sdk(
        self, pref_status=201, pref_response=None, pay_status=200, pay_response=None
    ):
        mock_sdk = MagicMock()
        mock_sdk.preference.return_value.create.return_value = {
            "status": pref_status,
            "response": pref_response
            or {
                "id": "pref-abc123",
                "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=pref-abc123",
            },
        }
        mock_sdk.payment.return_value.get.return_value = {
            "status": pay_status,
            "response": pay_response
            or {
                "id": 99999,
                "status": "approved",
                "external_reference": "42",
            },
        }
        return mock_sdk

    def test_create_preference_exitosa(self):
        mock_sdk = self._make_mock_sdk()
        with patch("gym.services.mercadopago_client._get_sdk", return_value=mock_sdk):
            result = create_payment_preference(self.member, payment_pk=42)

        self.assertTrue(result["ok"])
        self.assertEqual(result["preference_id"], "pref-abc123")
        self.assertIn("mercadopago.com.ar", result["init_point"])

    def test_create_preference_sin_access_token(self):
        with self.settings(MERCADOPAGO_ACCESS_TOKEN=""):
            result = create_payment_preference(self.member, payment_pk=42)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_access_token")

    def test_create_preference_mp_devuelve_error(self):
        mock_sdk = self._make_mock_sdk(
            pref_status=400, pref_response={"message": "bad request"}
        )
        with patch("gym.services.mercadopago_client._get_sdk", return_value=mock_sdk):
            result = create_payment_preference(self.member, payment_pk=42)

        self.assertFalse(result["ok"])
        self.assertIn("mp_status_400", result["error"])

    def test_get_payment_info_exitosa(self):
        mock_sdk = self._make_mock_sdk()
        with patch("gym.services.mercadopago_client._get_sdk", return_value=mock_sdk):
            result = get_payment_info("99999")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["mp_payment_id"], "99999")


class MercadoPagoWebhookTest(TestCase):
    MP_WEBHOOK_URL = "/api/webhooks/mercadopago/"

    def setUp(self):
        owner = User.objects.create_user(username="mp_webhook_owner", password="pass")
        self.tenant = Tenant.objects.create(
            name="Gym Webhook", owner=owner, admin_phone="5491100000000"
        )
        self.plan = SubscriptionPlan.objects.create(
            tenant=self.tenant,
            name="Mensual",
            duration_days=30,
            price=Decimal("5000.00"),
        )
        self.member = Member.objects.create(
            tenant=self.tenant,
            first_name="Carlos",
            last_name="Gomez",
            phone="5491177777777",
            current_plan=self.plan,
            start_date=date(2026, 1, 1),
            due_date=date(2026, 6, 27),
        )
        self.payment = Payment.objects.create(
            member=self.member,
            amount=Decimal("5000.00"),
            provider=Payment.PROVIDER_MP,
            status=Payment.STATUS_PENDING,
        )

    def _post(self, payload):
        return self.client.post(
            self.MP_WEBHOOK_URL,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _mock_get_payment(self, status="approved", external_ref=None):
        return patch(
            "gym.views.mercadopago_client.get_payment_info",
            return_value={
                "ok": True,
                "status": status,
                "external_reference": external_ref or str(self.payment.pk),
                "mp_payment_id": "99999",
            },
        )

    @patch(
        "gym.views.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-confirm-1", "data": {}},
    )
    def test_pago_aprobado_actualiza_payment_y_due_date(self, mock_send):
        original_due = self.member.due_date
        with self._mock_get_payment():
            resp = self._post({"type": "payment", "data": {"id": "99999"}})

        self.assertEqual(resp.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.payment.external_id, "99999")

        self.member.refresh_from_db()
        self.assertEqual(self.member.due_date, original_due + timedelta(days=30))
        self.assertEqual(self.member.status, Member.STATUS_ACTIVE)

        mock_send.assert_called_once()
        log = NotificationLog.objects.get(
            member=self.member, type=NotificationLog.TYPE_PAYMENT_CONFIRMED
        )
        self.assertEqual(log.job_id, "job-confirm-1")

    @patch(
        "gym.views.send_whatsapp_message",
        return_value={"ok": True, "job_id": "job-confirm-2", "data": {}},
    )
    def test_webhook_duplicado_no_renueva_dos_veces(self, mock_send):
        self.payment.status = Payment.STATUS_PAID
        self.payment.save()
        original_due = self.member.due_date

        with self._mock_get_payment():
            resp = self._post({"type": "payment", "data": {"id": "99999"}})

        self.assertEqual(resp.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.due_date, original_due)
        mock_send.assert_not_called()

    def test_pago_no_aprobado_no_procesa(self):
        with self._mock_get_payment(status="pending"):
            resp = self._post({"type": "payment", "data": {"id": "99999"}})

        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PENDING)

    def test_tipo_no_payment_ignorado(self):
        resp = self._post({"type": "merchant_order", "data": {"id": "99999"}})
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PENDING)

    def test_payload_invalido_devuelve_400(self):
        resp = self.client.post(
            self.MP_WEBHOOK_URL,
            data="no-json{",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
