const DJANGO_WEBHOOK_URL = process.env.DJANGO_WEBHOOK_URL
const SHARED_TOKEN = process.env.SHARED_TOKEN || ''

/**
 * Notifica a Django sobre eventos de sesión o entrega de mensajes.
 * Si DJANGO_WEBHOOK_URL no está configurada, loguea y sale sin error.
 *
 * Payload enviado: { tenant_id, event, data }
 * Eventos posibles: 'session_connected' | 'session_disconnected' | 'message_delivered'
 */
export async function notifyDjango(tenantId, event, data = {}) {
  if (!DJANGO_WEBHOOK_URL) {
    console.warn(`[webhook] DJANGO_WEBHOOK_URL no configurada — evento ${event} omitido`)
    return
  }
  try {
    await fetch(DJANGO_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Internal-Token': SHARED_TOKEN,
      },
      body: JSON.stringify({ tenant_id: tenantId, event, data }),
    })
  } catch (err) {
    console.error(`[webhook] Error al notificar Django (tenant=${tenantId}, event=${event}): ${err.message}`)
  }
}
