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
  - admin_phone (CharField, blank) — WhatsApp del admin para notificaciones internas

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
  - type: due_soon | due_today | expired | payment_confirmed
  - sent_at, delivered (bool), whatsapp_message_id
  - job_id (CharField, db_index) — correlaciona con el job_id del servicio Node
```

## Flujo diario (Celery Beat)

Task `check_subscriptions` corre 2 veces al día (09:00 y 18:00 UTC, vía Celery Beat):
1. Busca `Member` con `due_date == hoy` y `tenant__is_active=True`.
2. Omite socios que ya tengan un `NotificationLog(type='due_today')` del mismo día (idempotencia).
3. Por cada socio sin notificar:
   - Envía WhatsApp al socio avisando el vencimiento (`send_whatsapp_message`).
   - Envía WhatsApp al admin del tenant (`Tenant.admin_phone`) con el nombre del socio.
   - Registra ambos envíos en `NotificationLog(type='due_today')` con el `job_id` devuelto por Node.
4. Un fallo de envío individual (WhatsApp caído, etc.) no interrumpe el batch — se loguea y continúa.

Nota: el link de pago se agrega en la próxima iteración (integración Mercado Pago / Stripe).

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
| `message_sent` | `NotificationLog.whatsapp_message_id = data.whatsapp_message_id` (por `job_id`) |
| `message_delivered` | `NotificationLog.delivered = True` (por `whatsapp_message_id`) |

### ⚠️ Convención crítica: tenant_id
Node usa `tenant_id` como string opaco. **Debe ser siempre el `Tenant.pk` de Django** (entero
representado como string), no un slug ni nombre. Si se usa cualquier otro valor, el webhook de
Django no puede hacer el lookup en base de datos y loguea un warning silencioso.

### Estado de implementación: message_sent y correlación de mensajes

Django ya maneja el evento `message_sent` (implementado en `feat/django-whatsapp-communication`):
cuando Node reporta `{ event: "message_sent", data: { job_id, whatsapp_message_id } }`,
Django actualiza `NotificationLog.whatsapp_message_id` para el log con ese `job_id`.

El flujo completo de correlación es:
1. Django llama a Node con `POST /send-message` → Node devuelve `{ job_id }`.
2. Django guarda `job_id` en `NotificationLog.job_id`.
3. Node envía el mensaje y reporta `message_sent` a Django con `{ job_id, whatsapp_message_id }`.
4. Django actualiza `NotificationLog.whatsapp_message_id` usando el `job_id` como clave.
5. Al llegar `message_delivered`, Django busca por `whatsapp_message_id` y marca `delivered=True`.

Para que el paso 3 funcione, Node debe capturar el ID retornado por `sock.sendMessage()` en `queue.js`
y llamar al webhook de Django. Hasta que Node implemente eso, `message_delivered` llega pero no
actualiza ningún registro. Los eventos `session_connected` y `session_disconnected` funcionan sin
dependencia de esto.

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
