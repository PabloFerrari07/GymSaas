import { getSession } from './sessions.js'

// tenant_id → Array<{ jobId, phone, message, retries }>
const queues = new Map()
// tenant_ids actualmente procesando (para evitar dobles consumidores)
const processing = new Set()

const MAX_RETRIES = 3
const SESSION_WAIT_TIMEOUT_MS = 30_000

function randomDelayMs() {
  // 2 a 5 segundos entre mensajes para no disparar detección de spam
  return Math.floor(Math.random() * 3000) + 2000
}

export function enqueue(tenantId, phone, message) {
  if (!queues.has(tenantId)) queues.set(tenantId, [])

  const jobId = `${tenantId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  queues.get(tenantId).push({ jobId, phone, message, retries: 0 })

  if (!processing.has(tenantId)) {
    processQueue(tenantId)
  }
  return jobId
}

async function waitForSession(tenantId) {
  const deadline = Date.now() + SESSION_WAIT_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (getSession(tenantId)?.status === 'connected') return true
    await sleep(2000)
  }
  return false
}

async function processQueue(tenantId) {
  processing.add(tenantId)
  const queue = queues.get(tenantId)

  while (queue && queue.length > 0) {
    const job = queue[0]

    const ready = await waitForSession(tenantId)
    if (!ready) {
      console.warn(`[queue] Sesión no disponible para tenant=${tenantId} tras ${SESSION_WAIT_TIMEOUT_MS / 1000}s — reintentando`)
      await sleep(10_000)
      continue
    }

    const sock = getSession(tenantId).sock

    try {
      const jid = `${job.phone}@s.whatsapp.net`
      await sock.sendMessage(jid, { text: job.message })
      console.log(`[queue] ✓ Enviado a ${job.phone} (tenant=${tenantId}, job=${job.jobId})`)
      queue.shift()
    } catch (err) {
      job.retries++
      if (job.retries >= MAX_RETRIES) {
        console.error(`[queue] ✗ Mensaje a ${job.phone} falló ${MAX_RETRIES} veces, descartando (job=${job.jobId})`)
        queue.shift()
      } else {
        console.warn(`[queue] Error enviando a ${job.phone} (intento ${job.retries}/${MAX_RETRIES}): ${err.message}`)
        await sleep(5000)
        continue
      }
    }

    if (queue.length > 0) {
      await sleep(randomDelayMs())
    }
  }

  processing.delete(tenantId)
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}
