import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys'
import pino from 'pino'
import { notifyDjango } from './webhook.js'

// tenant_id → { sock, status: 'pending_qr'|'connected'|'disconnected', qr: string|null }
const sessions = new Map()

// Logger silenciado: Baileys es muy verboso por defecto.
const silentLogger = pino({ level: 'silent' })

export function getSession(tenantId) {
  return sessions.get(tenantId) ?? null
}

export function getQR(tenantId) {
  return sessions.get(tenantId)?.qr ?? null
}

export async function startSession(tenantId) {
  const existing = sessions.get(tenantId)
  if (existing?.status === 'connected') return

  const entry = { sock: null, status: 'pending_qr', qr: null }
  sessions.set(tenantId, entry)

  const { state, saveCreds } = await useMultiFileAuthState(`/app/sessions/${tenantId}`)

  const sock = makeWASocket({
    auth: state,
    logger: silentLogger,
    printQRInTerminal: false,
    browser: ['GymSaaS', 'Chrome', '1.0.0'],
  })
  entry.sock = sock

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update
    const session = sessions.get(tenantId)
    if (!session) return

    if (qr) {
      session.qr = qr
      session.status = 'pending_qr'
      console.log(`[${tenantId}] QR disponible — GET /qr/${tenantId}`)
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode
      const loggedOut = code === DisconnectReason.loggedOut

      console.log(`[${tenantId}] Sesión cerrada (code=${code}, loggedOut=${loggedOut})`)
      session.status = 'disconnected'
      session.qr = null

      await notifyDjango(tenantId, 'session_disconnected', { status_code: code })

      if (!loggedOut) {
        // Reconectar si fue un cierre inesperado (no un logout deliberado)
        console.log(`[${tenantId}] Reconectando en 5s...`)
        setTimeout(() => startSession(tenantId), 5000)
      }
    }

    if (connection === 'open') {
      session.status = 'connected'
      session.qr = null
      console.log(`[${tenantId}] Sesión conectada ✓`)
      await notifyDjango(tenantId, 'session_connected', {})
    }
  })

  // Reportar entrega de mensajes a Django (status >= 4 = DELIVERY_ACK o superior)
  sock.ev.on('messages.update', async (updates) => {
    for (const upd of updates) {
      if ((upd.update.status ?? 0) >= 4) {
        await notifyDjango(tenantId, 'message_delivered', {
          whatsapp_message_id: upd.key.id,
          status: upd.update.status,
        })
      }
    }
  })
}
