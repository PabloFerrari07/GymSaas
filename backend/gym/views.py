import json
import logging
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import NotificationLog, Payment, WhatsAppSession
from .services import mercadopago_client
from .services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    """
    Recibe eventos del servicio de WhatsApp (Node + Baileys).
    Autenticado por token compartido en el header X-Internal-Token.

    Ciclo de vida de un mensaje:
      1. message_sent      → Baileys confirmó envío; puebla NotificationLog.whatsapp_message_id
                             correlacionando por job_id.
      2. message_delivered → WhatsApp confirmó entrega al dispositivo; pone delivered=True
                             correlacionando por whatsapp_message_id.
      - session_connected / session_disconnected → actualiza WhatsAppSession.status
    """
    token = request.headers.get("X-Internal-Token", "")
    if not settings.WHATSAPP_SHARED_TOKEN or token != settings.WHATSAPP_SHARED_TOKEN:
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    tenant_id = payload.get("tenant_id")
    event = payload.get("event")
    data = payload.get("data", {})

    if not tenant_id or not event:
        return JsonResponse({"error": "tenant_id y event son requeridos"}, status=400)

    if event in ("session_connected", "session_disconnected"):
        _handle_session_event(tenant_id, event)
    elif event == "message_sent":
        _handle_message_sent(tenant_id, data)
    elif event == "message_delivered":
        _handle_message_delivered(tenant_id, data)
    else:
        logger.info("Webhook: evento desconocido '%s' (tenant=%s)", event, tenant_id)

    return JsonResponse({"ok": True})


def _handle_session_event(tenant_id, event):
    new_status = (
        WhatsAppSession.STATUS_CONNECTED
        if event == "session_connected"
        else WhatsAppSession.STATUS_DISCONNECTED
    )
    try:
        updated = WhatsAppSession.objects.filter(tenant_id=int(tenant_id)).update(
            status=new_status
        )
    except (ValueError, TypeError):
        logger.error("Webhook: tenant_id inválido '%s' en evento %s", tenant_id, event)
        return

    if not updated:
        logger.warning(
            "Webhook: no se encontró WhatsAppSession para tenant_id=%s (evento=%s)",
            tenant_id,
            event,
        )


def _handle_message_sent(tenant_id, data):
    job_id = data.get("job_id")
    whatsapp_message_id = data.get("whatsapp_message_id")
    if not job_id or not whatsapp_message_id:
        logger.warning(
            "Webhook: message_sent con datos incompletos (tenant=%s, data=%s)",
            tenant_id,
            data,
        )
        return

    updated = NotificationLog.objects.filter(
        job_id=job_id,
        member__tenant_id=tenant_id,
    ).update(whatsapp_message_id=whatsapp_message_id)

    if not updated:
        logger.warning(
            "Webhook: no se encontró NotificationLog con job_id=%s (tenant=%s)",
            job_id,
            tenant_id,
        )


def _handle_message_delivered(tenant_id, data):
    whatsapp_message_id = data.get("whatsapp_message_id")
    if not whatsapp_message_id:
        logger.warning(
            "Webhook: message_delivered sin whatsapp_message_id (tenant=%s)", tenant_id
        )
        return

    updated = NotificationLog.objects.filter(
        whatsapp_message_id=whatsapp_message_id,
        member__tenant_id=tenant_id,
    ).update(delivered=True)

    if not updated:
        logger.warning(
            "Webhook: no se encontró NotificationLog con whatsapp_message_id=%s (tenant=%s)",
            whatsapp_message_id,
            tenant_id,
        )


@csrf_exempt
@require_POST
def mercadopago_webhook(request):
    """
    Recibe notificaciones de Mercado Pago cuando un pago cambia de estado.
    MP no usa nuestro token interno — la identidad se valida por firma HMAC
    si MERCADOPAGO_WEBHOOK_SECRET está configurado, o se acepta sin firma en dev.

    Idempotente: si el Payment ya está en status='paid', retorna 200 sin reprocesar.
    MP requiere respuesta HTTP 200 rápida — el procesamiento es síncrono y breve.
    """
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    if not _verify_mp_signature(request, payload):
        logger.warning("Webhook MP: firma inválida rechazada")
        return JsonResponse({"error": "invalid signature"}, status=400)

    notification_type = payload.get("type")
    data = payload.get("data", {})
    mp_payment_id = str(data.get("id", ""))

    if notification_type != "payment" or not mp_payment_id:
        # MP también manda notificaciones de otros tipos (merchant_order, etc.) — ignorarlas silenciosamente
        return JsonResponse({"ok": True})

    payment_info = mercadopago_client.get_payment_info(mp_payment_id)
    if not payment_info["ok"]:
        logger.error(
            "Webhook MP: no se pudo consultar pago id=%s: %s",
            mp_payment_id,
            payment_info.get("error"),
        )
        return JsonResponse({"error": "mp_api_error"}, status=502)

    if payment_info["status"] != "approved":
        logger.info(
            "Webhook MP: pago id=%s en estado '%s', ignorando",
            mp_payment_id,
            payment_info["status"],
        )
        return JsonResponse({"ok": True})

    external_ref = payment_info["external_reference"]
    if not external_ref:
        logger.error(
            "Webhook MP: pago aprobado sin external_reference (mp_id=%s)", mp_payment_id
        )
        return JsonResponse({"error": "no_external_reference"}, status=400)

    try:
        payment = Payment.objects.select_related(
            "member__tenant", "member__current_plan"
        ).get(pk=int(external_ref))
    except (Payment.DoesNotExist, ValueError):
        logger.error("Webhook MP: no se encontró Payment con pk=%s", external_ref)
        return JsonResponse({"error": "payment_not_found"}, status=404)

    # Idempotencia: si ya está pagado, no reprocesar
    if payment.status == Payment.STATUS_PAID:
        logger.info(
            "Webhook MP: pago pk=%d ya estaba pagado, ignorando duplicado", payment.pk
        )
        return JsonResponse({"ok": True})

    # Marcar como pagado
    now = timezone.now()
    payment.status = Payment.STATUS_PAID
    payment.paid_at = now
    payment.external_id = payment_info["mp_payment_id"]
    payment.save(update_fields=["status", "paid_at", "external_id"])

    # Renovar fecha de vencimiento del socio
    member = payment.member
    plan = member.current_plan
    if plan:
        base_date = member.due_date or now.date()
        member.due_date = base_date + timedelta(days=plan.duration_days)
        member.status = member.STATUS_ACTIVE
        member.save(update_fields=["due_date", "status"])

    # WhatsApp de confirmación al socio
    _send_payment_confirmation_whatsapp(member)

    return JsonResponse({"ok": True})


def _verify_mp_signature(request, payload: dict) -> bool:
    """
    Verifica la firma HMAC-SHA256 que Mercado Pago envía en el header x-signature.
    Si MERCADOPAGO_WEBHOOK_SECRET no está configurado, omite la validación (modo dev).
    Formato del header: ts=<timestamp>,v1=<hmac-sha256-hex>
    Manifest: id:<data.id>;request-id:<x-request-id>;ts:<timestamp>
    """
    import hashlib
    import hmac as hmaclib

    secret = settings.MERCADOPAGO_WEBHOOK_SECRET
    if not secret:
        return True  # dev: sin validación de firma

    signature_header = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    data_id = str(payload.get("data", {}).get("id", ""))

    parts = {}
    for part in signature_header.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("ts", "")
    v1 = parts.get("v1", "")
    if not ts or not v1:
        return False

    manifest = f"id:{data_id};request-id:{request_id};ts:{ts}"
    expected = hmaclib.new(
        secret.encode(), manifest.encode(), hashlib.sha256
    ).hexdigest()
    return hmaclib.compare_digest(expected, v1)


def _send_payment_confirmation_whatsapp(member):
    tenant = member.tenant
    message = (
        f"Hola {member.first_name}! Tu pago en {tenant.name} fue confirmado. "
        f"Tu cuota esta al dia hasta el {member.due_date.strftime('%d/%m/%Y')}. Gracias!"
    )
    result = send_whatsapp_message(
        tenant_id=tenant.pk,
        phone=member.phone,
        message=message,
    )
    if not result["ok"]:
        logger.error(
            "Fallo al enviar confirmacion de pago a socio pk=%d: %s",
            member.pk,
            result.get("error"),
        )
        return
    NotificationLog.objects.create(
        member=member,
        type=NotificationLog.TYPE_PAYMENT_CONFIRMED,
        job_id=result.get("job_id", ""),
    )
