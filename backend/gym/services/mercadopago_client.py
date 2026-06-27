import logging

import mercadopago
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_sdk():
    return mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)


def create_payment_preference(member, payment_pk: int) -> dict:
    """
    Crea una preferencia de Checkout Pro en Mercado Pago para el member dado.

    Retorno exitoso:  {'ok': True, 'init_point': '<url>', 'preference_id': '<id>'}
    Retorno fallido:  {'ok': False, 'error': '<razón>'}

    payment_pk se incluye como external_reference para poder correlacionar el
    webhook de pago con nuestro Payment sin llamadas extra a la API de MP.
    Nunca lanza excepción — seguro llamar desde Celery tasks.
    """
    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        logger.error("MERCADOPAGO_ACCESS_TOKEN no configurado")
        return {"ok": False, "error": "no_access_token"}

    plan = member.current_plan
    if not plan:
        logger.error("Socio pk=%d no tiene plan vigente", member.pk)
        return {"ok": False, "error": "no_plan"}

    preference_data = {
        "items": [
            {
                "title": f"Cuota {plan.name} — {member.tenant.name}",
                "quantity": 1,
                "unit_price": float(plan.price),
                "currency_id": "ARS",
            }
        ],
        "external_reference": str(payment_pk),
    }

    try:
        sdk = _get_sdk()
        response = sdk.preference().create(preference_data)
    except Exception as exc:
        logger.exception(
            "Error al crear preferencia MP para socio pk=%d: %s", member.pk, exc
        )
        return {"ok": False, "error": str(exc)}

    if response.get("status") not in (200, 201):
        logger.error(
            "MP devolvió status %s al crear preferencia (socio pk=%d): %s",
            response.get("status"),
            member.pk,
            response.get("response"),
        )
        return {"ok": False, "error": f"mp_status_{response.get('status')}"}

    preference = response["response"]
    return {
        "ok": True,
        "init_point": preference["init_point"],
        "preference_id": preference["id"],
    }


def get_payment_info(mp_payment_id: str) -> dict:
    """
    Consulta el estado de un pago en MP por su ID (el que llega en el webhook).

    Retorno exitoso:  {'ok': True, 'status': '<mp_status>', 'external_reference': '<str>',
                       'mp_payment_id': '<str>'}
    Retorno fallido:  {'ok': False, 'error': '<razón>'}

    Nunca lanza excepción — seguro llamar desde vistas.
    """
    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        return {"ok": False, "error": "no_access_token"}

    try:
        sdk = _get_sdk()
        response = sdk.payment().get(mp_payment_id)
    except Exception as exc:
        logger.exception("Error al consultar pago MP id=%s: %s", mp_payment_id, exc)
        return {"ok": False, "error": str(exc)}

    if response.get("status") not in (200, 201):
        logger.error(
            "MP devolvió status %s al consultar pago id=%s: %s",
            response.get("status"),
            mp_payment_id,
            response.get("response"),
        )
        return {"ok": False, "error": f"mp_status_{response.get('status')}"}

    payment = response["response"]
    return {
        "ok": True,
        "status": payment.get("status", ""),
        "external_reference": payment.get("external_reference", ""),
        "mp_payment_id": str(payment.get("id", "")),
    }
