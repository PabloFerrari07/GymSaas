import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # segundos


def send_whatsapp_message(tenant_id: int | str, phone: str, message: str) -> dict:
    """
    Encola un mensaje en el servicio de WhatsApp (Node + Baileys).

    Devuelve {'ok': True, 'data': {...}} o {'ok': False, 'error': '<razón>'}.
    Nunca lanza excepción — es seguro llamar desde tareas de Celery.

    Args:
        tenant_id: PK del Tenant en Django (el servicio Node lo usa como identificador opaco).
        phone: Número en formato internacional sin '+' (ej. '5491112345678').
        message: Texto plano a enviar.
    """
    url = f"{settings.WHATSAPP_SERVICE_URL}/send-message"
    try:
        resp = requests.post(
            url,
            json={"tenant_id": str(tenant_id), "phone": phone, "message": message},
            headers={"X-Internal-Token": settings.WHATSAPP_SHARED_TOKEN},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return {"ok": True, "data": resp.json()}
    except requests.exceptions.ConnectionError as exc:
        logger.error("WhatsApp service unreachable (url=%s): %s", url, exc)
        return {"ok": False, "error": "connection_error"}
    except requests.exceptions.Timeout:
        logger.error("WhatsApp service timeout (url=%s)", url)
        return {"ok": False, "error": "timeout"}
    except requests.exceptions.HTTPError as exc:
        logger.error("WhatsApp service HTTP error (url=%s): %s", url, exc)
        return {"ok": False, "error": f"http_{resp.status_code}"}
    except Exception as exc:
        logger.exception(
            "Unexpected error calling WhatsApp service (url=%s): %s", url, exc
        )
        return {"ok": False, "error": str(exc)}
