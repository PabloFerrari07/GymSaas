from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gym", "0002_notificationlog_job_id")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="admin_phone",
            field=models.CharField(
                blank=True,
                max_length=30,
                help_text="Número WhatsApp del admin para notificaciones internas (ej. 5491112345678)",
            ),
        ),
    ]
