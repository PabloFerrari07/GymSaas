from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from gym.models import Member, SubscriptionPlan, Tenant


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
