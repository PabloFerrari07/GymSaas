from datetime import timedelta

from django.db import models
from django.contrib.auth.models import User


class Tenant(models.Model):
    PLAN_FREE = "free"
    PLAN_BASIC = "basic"
    PLAN_PRO = "pro"
    PLAN_CHOICES = [(PLAN_FREE, "Free"), (PLAN_BASIC, "Basic"), (PLAN_PRO, "Pro")]

    name = models.CharField(max_length=200)
    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name="tenant")
    plan_saas = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_FREE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"


class WhatsAppSession(models.Model):
    STATUS_PENDING_QR = "pending_qr"
    STATUS_CONNECTED = "connected"
    STATUS_DISCONNECTED = "disconnected"
    STATUS_CHOICES = [
        (STATUS_PENDING_QR, "Pending QR"),
        (STATUS_CONNECTED, "Connected"),
        (STATUS_DISCONNECTED, "Disconnected"),
    ]

    tenant = models.OneToOneField(
        Tenant, on_delete=models.CASCADE, related_name="whatsapp_session"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING_QR
    )
    phone_number = models.CharField(max_length=30, blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.tenant.name} — {self.status}"

    class Meta:
        verbose_name = "WhatsApp Session"
        verbose_name_plural = "WhatsApp Sessions"


class SubscriptionPlan(models.Model):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="subscription_plans"
    )
    name = models.CharField(max_length=100)
    duration_days = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.tenant.name} — {self.name}"

    class Meta:
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"


class Member(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_DUE_SOON = "due_soon"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_DUE_SOON, "Due Soon"),
        (STATUS_EXPIRED, "Expired"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="members")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30)
    current_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    start_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE
    )

    def save(self, *args, **kwargs):
        if self.current_plan and not self.due_date:
            self.due_date = self.start_date + timedelta(
                days=self.current_plan.duration_days
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.tenant.name})"

    class Meta:
        verbose_name = "Member"
        verbose_name_plural = "Members"


class Payment(models.Model):
    PROVIDER_MP = "mercadopago"
    PROVIDER_STRIPE = "stripe"
    PROVIDER_CHOICES = [(PROVIDER_MP, "Mercado Pago"), (PROVIDER_STRIPE, "Stripe")]

    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_EXPIRED, "Expired"),
    ]

    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_link = models.URLField(blank=True)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    external_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Payment {self.id} — {self.member} — {self.status}"

    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"


class NotificationLog(models.Model):
    TYPE_DUE_SOON = "due_soon"
    TYPE_EXPIRED = "expired"
    TYPE_PAYMENT_CONFIRMED = "payment_confirmed"
    TYPE_CHOICES = [
        (TYPE_DUE_SOON, "Due Soon"),
        (TYPE_EXPIRED, "Expired"),
        (TYPE_PAYMENT_CONFIRMED, "Payment Confirmed"),
    ]

    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="notification_logs"
    )
    type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    sent_at = models.DateTimeField(auto_now_add=True)
    delivered = models.BooleanField(default=False)
    whatsapp_message_id = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.type} — {self.member} — {self.sent_at.date()}"

    class Meta:
        verbose_name = "Notification Log"
        verbose_name_plural = "Notification Logs"
