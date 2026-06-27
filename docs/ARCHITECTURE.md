# ARCHITECTURE — SaaS de gestión de gimnasios

## Visión general
Dos servicios separados que se comunican por HTTP interno:

1. **Backend principal (Django + DRF)**: fuente de verdad de datos, lógica de negocio, jobs programados, webhooks de pago.
2. **Servicio de WhatsApp (Node.js + Baileys)**: mantiene las sesiones de WhatsApp (una por tenant/gimnasio) y expone un endpoint interno para enviar mensajes.

No van en el mismo proceso. Baileys mantiene un socket persistente por sesión activa, lo cual no convive bien con el modelo request/response de Django. Separarlos también permite reiniciar el servicio de WhatsApp (por ejemplo, tras una desconexión) sin afectar el backend.

## Stack
- Backend: Django + DRF, Celery + Celery Beat para jobs programados, Postgres.
- WhatsApp: Node.js, librería Baileys (no oficial, gratis, basada en simular un cliente de WhatsApp Web).
- Pagos: Mercado Pago Checkout Pro y/o Stripe Payment Links, vía webhooks.
- Multi-tenancy: por columna (`tenant_id` en cada tabla relevante), no por schema separado. Suficiente para la escala esperada del MVP.

## Modelos (Django)

```
Tenant (Gym)
  - name, owner (FK User), plan_saas, is_active, created_at

WhatsAppSession
  - tenant (FK OneToOne)
  - status: pending_qr | connected | disconnected
  - phone_number
  - last_connected_at

SubscriptionPlan   # tipo de cuota que el gym le vende a sus socios
  - tenant (FK)
  - name, duration_days, price

Member   # socio del gimnasio
  - tenant (FK)
  - first_name, last_name, phone
  - current_plan (FK SubscriptionPlan)
  - start_date, due_date
  - status: active | due_soon | expired

Payment   # cobro generado para un socio
  - member (FK)
  - amount, payment_link, provider (mercadopago | stripe)
  - status: pending | paid | expired
  - external_id
  - created_at, paid_at

NotificationLog
  - member (FK)
  - type: due_soon | expired | payment_confirmed
  - sent_at, delivered (bool), whatsapp_message_id
```

## Flujo diario (Celery Beat)

Task `check_subscriptions` corre una vez al día:
1. Busca `Member` con `due_date` en ventana configurada (ej. -1 día / hoy / +3 días).
2. Por cada uno:
   - Genera `Payment` con link de pago (Mercado Pago o Stripe).
   - POST al servicio Node: enviar WhatsApp al socio (aviso + link).
   - POST al servicio Node: enviar WhatsApp al admin del tenant (aviso interno).
   - Registra todo en `NotificationLog`.

## Webhooks

- **Webhook de Mercado Pago / Stripe → Django**: al confirmarse el pago, actualiza `Payment.status = paid`, recalcula `Member.due_date` (`+duration_days` del plan), y dispara WhatsApp de confirmación al socio.
- **Webhook del servicio Node → Django**: confirma entrega de mensaje, o avisa caída de sesión de WhatsApp (para alertar al admin que debe re-escanear el QR).

## Servicio de WhatsApp (Node + Baileys)

- Una sesión de Baileys = un número de WhatsApp = un tenant. El dueño del gym escanea el QR una sola vez al alta (re-escanea si la sesión se cae).
- Endpoint interno `POST /send-message`: recibe `tenant_id`, `phone`, `message` (o `template` + `params`), encola el envío.
- Rate limiting propio: delay de 2-5 segundos entre envíos por sesión para evitar baneo del número.
- Cola de reintentos: si falla el envío (sesión caída, error de red), el mensaje se reencola en vez de perderse.
- Expone webhook hacia Django para reportar estado de entrega y caídas de sesión.

## Decisión registrada: por qué Baileys y no la API oficial de Meta en el MVP
La API oficial de WhatsApp Business no es cara en sí (categoría utility, centavos por mensaje, conversaciones de servicio gratis), pero requiere un Business Solution Provider (BSP) con suscripción/markup, y proceso de aprobación. Para validar el producto con pocos tenants reales, se prioriza velocidad y costo cero con Baileys. Migración a API oficial vía BSP queda planificada para cuando haya tenants pagando (ver PRD.md, fuera de alcance del MVP).

## Contrato Django ↔ Node (implementado)

### Django → Node
`POST {WHATSAPP_SERVICE_URL}/send-message`
```json
{ "tenant_id": "<Tenant.pk como string>", "phone": "5491112345678", "message": "texto" }
```
Header: `X-Internal-Token: <WHATSAPP_SHARED_TOKEN>`
Respuesta: `{ "status": "queued", "job_id": "..." }`

Implementado en `gym/services/whatsapp_client.py` → `send_whatsapp_message()`.
Nunca lanza excepción (safe para llamar desde Celery tasks).

### Node → Django
`POST {DJANGO_WEBHOOK_URL}` (= `/api/webhooks/whatsapp/`)
```json
{ "tenant_id": "<Tenant.pk>", "event": "<nombre>", "data": {} }
```
Header: `X-Internal-Token: <SHARED_TOKEN>`

Eventos implementados en Django:
| Evento | Acción en Django |
|---|---|
| `session_connected` | `WhatsAppSession.status = connected` |
| `session_disconnected` | `WhatsAppSession.status = disconnected` |
| `message_delivered` | `NotificationLog.delivered = True` (por `whatsapp_message_id`) |

### ⚠️ Convención crítica: tenant_id
Node usa `tenant_id` como string opaco. **Debe ser siempre el `Tenant.pk` de Django** (entero
representado como string), no un slug ni nombre. Si se usa cualquier otro valor, el webhook de
Django no puede hacer el lookup en base de datos y loguea un warning silencioso.

### Pendiente del lado de Node para cerrar message_delivered

Hoy `queue.js` envía el mensaje con `sock.sendMessage(jid, { text })` pero **no reporta el
`whatsapp_message_id` asignado por Baileys a Django**. `sendMessage` devuelve el mensaje
con su clave/ID, que es lo que luego llega en el evento `messages.update`.

Para que `message_delivered` pueda correlacionarse con un `NotificationLog` específico, Node
necesita:
1. Capturar el ID de mensaje retornado por `sock.sendMessage()` en `queue.js`.
2. Llamar a Django (o incluirlo en la respuesta al encolado) para que Django guarde ese ID
   en `NotificationLog.whatsapp_message_id` al momento del envío.

Hasta que esto esté implementado, el evento `message_delivered` llega correctamente a Django
pero no actualiza ningún registro (el `filter()` no encuentra coincidencias). Los demás
eventos (`session_connected`, `session_disconnected`) funcionan sin dependencia de esto.

## Próximos pasos técnicos (orden sugerido)
1. Modelos Django + admin básico. ✓
2. Servicio Node con Baileys: conexión, envío de mensaje simple, QR de alta. ✓
3. Comunicación Django ↔ Node (endpoint interno + auth simple por token compartido). ✓
4. Job de Celery Beat de chequeo de vencimientos.
5. Integración de Mercado Pago / Stripe (generación de link + webhook).
6. Dashboard en React (ver sección Frontend más abajo).

## Frontend (decisión registrada, se implementa al final)
El dashboard del admin de gimnasio se construye en React puro, consumiendo la API de DRF — no server-rendered con templates de Django.

- Carpeta separada `frontend/`, como tercer proyecto del repo junto a `backend/` y `whatsapp-service/`.
- Requiere agregar autenticación a la API (DRF, vía JWT o session auth) — hoy el único acceso al sistema es el admin de Django, esto es nuevo.
- Multi-tenancy en los endpoints expuestos al frontend: el admin logueado solo debe poder ver/crear datos de su propio tenant. El scoping por `tenant_id` ya existe a nivel modelo; falta garantizarlo a nivel de permisos/queryset en las vistas de DRF que consuma el frontend.
- No se implementa hasta tener los pasos 2-5 funcionando — recién ahí hay datos reales (socios, pagos, notificaciones) para mostrar en un dashboard con sentido.
