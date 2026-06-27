import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Tenant
        migrations.CreateModel(
            name="Tenant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "plan_saas",
                    models.CharField(
                        choices=[("free", "Free"), ("basic", "Basic"), ("pro", "Pro")],
                        default="free",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tenant",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Tenant",
                "verbose_name_plural": "Tenants",
            },
        ),
        # 2. WhatsAppSession (depends on Tenant)
        migrations.CreateModel(
            name="WhatsAppSession",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending_qr", "Pending QR"),
                            ("connected", "Connected"),
                            ("disconnected", "Disconnected"),
                        ],
                        default="pending_qr",
                        max_length=20,
                    ),
                ),
                ("phone_number", models.CharField(blank=True, max_length=30)),
                ("last_connected_at", models.DateTimeField(blank=True, null=True)),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="whatsapp_session",
                        to="gym.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "WhatsApp Session",
                "verbose_name_plural": "WhatsApp Sessions",
            },
        ),
        # 3. SubscriptionPlan (depends on Tenant)
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("duration_days", models.PositiveIntegerField()),
                ("price", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription_plans",
                        to="gym.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Subscription Plan",
                "verbose_name_plural": "Subscription Plans",
            },
        ),
        # 4. Member (depends on Tenant and SubscriptionPlan)
        migrations.CreateModel(
            name="Member",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("first_name", models.CharField(max_length=100)),
                ("last_name", models.CharField(max_length=100)),
                ("phone", models.CharField(max_length=30)),
                ("start_date", models.DateField()),
                ("due_date", models.DateField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("due_soon", "Due Soon"),
                            ("expired", "Expired"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="members",
                        to="gym.tenant",
                    ),
                ),
                (
                    "current_plan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="members",
                        to="gym.subscriptionplan",
                    ),
                ),
            ],
            options={
                "verbose_name": "Member",
                "verbose_name_plural": "Members",
            },
        ),
        # 5. Payment (depends on Member)
        migrations.CreateModel(
            name="Payment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("payment_link", models.URLField(blank=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[("mercadopago", "Mercado Pago"), ("stripe", "Stripe")],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("paid", "Paid"),
                            ("expired", "Expired"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("external_id", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payments",
                        to="gym.member",
                    ),
                ),
            ],
            options={
                "verbose_name": "Payment",
                "verbose_name_plural": "Payments",
            },
        ),
        # 6. NotificationLog (depends on Member)
        migrations.CreateModel(
            name="NotificationLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("due_soon", "Due Soon"),
                            ("expired", "Expired"),
                            ("payment_confirmed", "Payment Confirmed"),
                        ],
                        max_length=30,
                    ),
                ),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("delivered", models.BooleanField(default=False)),
                ("whatsapp_message_id", models.CharField(blank=True, max_length=200)),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_logs",
                        to="gym.member",
                    ),
                ),
            ],
            options={
                "verbose_name": "Notification Log",
                "verbose_name_plural": "Notification Logs",
            },
        ),
    ]
