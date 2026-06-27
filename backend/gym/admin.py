from django.contrib import admin
from .models import (
    Tenant,
    WhatsAppSession,
    SubscriptionPlan,
    Member,
    Payment,
    NotificationLog,
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "plan_saas",
        "is_active",
        "admin_phone",
        "created_at",
    )
    list_filter = ("plan_saas", "is_active")
    search_fields = ("name", "owner__username", "owner__email")


@admin.register(WhatsAppSession)
class WhatsAppSessionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "status", "phone_number", "last_connected_at")
    list_filter = ("status",)
    search_fields = ("tenant__name", "phone_number")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "duration_days", "price")
    list_filter = ("tenant",)
    search_fields = ("name", "tenant__name")


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = (
        "last_name",
        "first_name",
        "tenant",
        "current_plan",
        "due_date",
        "status",
        "phone",
    )
    list_filter = ("status", "tenant", "current_plan")
    search_fields = ("first_name", "last_name", "phone")
    date_hierarchy = "due_date"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "member",
        "amount",
        "provider",
        "status",
        "payment_link",
        "created_at",
        "paid_at",
    )
    list_filter = ("status", "provider")
    search_fields = ("member__first_name", "member__last_name", "external_id")
    date_hierarchy = "created_at"


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("member", "type", "sent_at", "delivered", "whatsapp_message_id")
    list_filter = ("type", "delivered")
    search_fields = ("member__first_name", "member__last_name")
    date_hierarchy = "sent_at"
