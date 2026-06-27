import logging

from celery import shared_task
from django.utils import timezone

from .models import Member, NotificationLog
from .services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)


@shared_task(name="gym.tasks.check_subscriptions")
def check_subscriptions():
    """
    Detecta socios con due_date == hoy y envía avisos por WhatsApp al socio y al admin.
    Corre 2 veces al día (09:00 y 18:00 UTC) vía Celery Beat.
    Idempotente: omite socios que ya tengan un NotificationLog de tipo
    'due_today' creado hoy (evita duplicados entre la corrida de mañana y tarde).
    """
    today = timezone.localdate()
    members = Member.objects.filter(
        due_date=today, tenant__is_active=True
    ).select_related("tenant", "current_plan")

    count = members.count()
    logger.info("check_subscriptions: %d miembro(s) con vencimiento %s", count, today)

    for member in members:
        already_notified = NotificationLog.objects.filter(
            member=member,
            type=NotificationLog.TYPE_DUE_TODAY,
            sent_at__date=today,
            job_id__gt="",
        ).exists()

        if already_notified:
            logger.info(
                "Socio %s (pk=%d) ya notificado hoy, omitiendo", member, member.pk
            )
            continue

        _notify_member(member)
        _notify_admin(member)


def _notify_member(member):
    tenant = member.tenant
    message = (
        f"Hola {member.first_name}! Tu cuota en {tenant.name} vence hoy. "
        f"Proximamente te contactamos para coordinar el pago. Gracias!"
    )
    result = send_whatsapp_message(
        tenant_id=tenant.pk,
        phone=member.phone,
        message=message,
    )
    if not result["ok"]:
        logger.error(
            "Fallo al notificar socio pk=%d (tenant=%d): %s",
            member.pk,
            tenant.pk,
            result.get("error"),
        )
    NotificationLog.objects.create(
        member=member,
        type=NotificationLog.TYPE_DUE_TODAY,
        job_id=result.get("job_id", ""),
    )


def _notify_admin(member):
    tenant = member.tenant
    admin_phone = tenant.admin_phone

    if not admin_phone:
        logger.warning(
            "Tenant pk=%d sin admin_phone — omitiendo notificacion al admin (socio pk=%d)",
            tenant.pk,
            member.pk,
        )
        return

    message = (
        f"[{tenant.name}] Vencimiento hoy: "
        f"{member.first_name} {member.last_name} (tel: {member.phone})"
    )
    result = send_whatsapp_message(
        tenant_id=tenant.pk,
        phone=admin_phone,
        message=message,
    )
    if not result["ok"]:
        logger.error(
            "Fallo al notificar admin del tenant pk=%d (socio pk=%d): %s",
            tenant.pk,
            member.pk,
            result.get("error"),
        )
    NotificationLog.objects.create(
        member=member,
        type=NotificationLog.TYPE_DUE_TODAY,
        job_id=result.get("job_id", ""),
    )
