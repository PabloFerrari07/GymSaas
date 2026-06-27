import json
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from gym.models import (
    Member,
    NotificationLog,
    SubscriptionPlan,
    Tenant,
    WhatsAppSession,
)

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
