import express from 'express'
import QRCode from 'qrcode'
import { startSession, getSession, getQR } from './sessions.js'
import { enqueue } from './queue.js'

const app = express()
app.use(express.json())

const PORT = process.env.PORT || 3000
const SHARED_TOKEN = process.env.SHARED_TOKEN || ''

function requireToken(req, res, next) {
  if (!SHARED_TOKEN) return next()
  if (req.headers['x-internal-token'] !== SHARED_TOKEN) {
    return res.status(401).json({ error: 'Token inválido' })
  }
  next()
}

// GET /health
// Chequeo simple de que el servicio está arriba.
app.get('/health', (_req, res) => {
  res.json({ status: 'ok', uptime_s: Math.floor(process.uptime()) })
})

// GET /qr/:tenant_id
// Si la sesión no existe, la inicia y devuelve el QR como imagen PNG.
// Si ya está conectada, responde con JSON indicando que no es necesario escanear.
// Si la sesión está arrancando pero el QR aún no se generó, devuelve 202.
app.get('/qr/:tenant_id', async (req, res) => {
  const { tenant_id } = req.params

  const session = getSession(tenant_id)
  if (session?.status === 'connected') {
    return res.json({
      status: 'connected',
      message: 'Sesión ya activa — no es necesario escanear QR',
    })
  }

  if (!session) {
    // Arrancar la sesión en background; el QR tarda ~2-4s en generarse
    startSession(tenant_id).catch(err =>
      console.error(`[${tenant_id}] Error iniciando sesión: ${err.message}`)
    )
    await new Promise(r => setTimeout(r, 4000))
  }

  const qr = getQR(tenant_id)
  if (!qr) {
    return res.status(202).json({
      status: 'starting',
      message: 'Sesión iniciando. Reintentá en 5 segundos.',
    })
  }

  try {
    const png = await QRCode.toBuffer(qr)
    res.setHeader('Content-Type', 'image/png')
    res.send(png)
  } catch {
    res.status(500).json({ error: 'Error generando imagen QR' })
  }
})

// POST /send-message
// Body: { tenant_id, phone, message }
// Encola el mensaje con rate limiting de 2-5s entre envíos de la misma sesión.
// Responde inmediatamente con el job_id; el envío ocurre de forma asíncrona.
app.post('/send-message', requireToken, (req, res) => {
  const { tenant_id, phone, message } = req.body ?? {}
  if (!tenant_id || !phone || !message) {
    return res.status(400).json({ error: 'Campos requeridos: tenant_id, phone, message' })
  }

  const jobId = enqueue(tenant_id, phone, message)
  res.status(202).json({ status: 'queued', job_id: jobId })
})

app.listen(PORT, () => {
  console.log(`[whatsapp-service] Escuchando en :${PORT}`)
})
