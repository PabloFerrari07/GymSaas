import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import NotificationLog, WhatsAppSession

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    """
    Recibe eventos del servicio de WhatsApp (Node + Baileys).
    Autenticado por token compartido en el header X-Internal-Token.

    Eventos soportados:
      - session_connected / session_disconnected → actualiza WhatsAppSession.status
      - message_delivered → marca NotificationLog.delivered = True
        (requiere que NotificationLog.whatsapp_message_id esté poblado —
        ver docs/ARCHITECTURE.md, sección "Pendiente del lado de Node")
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
