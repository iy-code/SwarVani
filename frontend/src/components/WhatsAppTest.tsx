import { useState } from 'react'
import { apiUrl } from '../lib/api'

export default function WhatsAppTest({ recordId }: { recordId: string }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState('')
  async function send() {
    setBusy(true)
    setResult('')
    try {
      const response = await fetch(apiUrl(`/api/whatsapp/test-followup/${encodeURIComponent(recordId)}`), {
        method: 'POST', headers: { 'X-SwarVani-Test': '1' },
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail || 'Could not send the test message.')
      setResult('Meta accepted the message. Check your test phone and tap a reply; the board refreshes automatically.')
    } catch (error) {
      setResult(error instanceof Error ? error.message : 'Connection failed. Check WhatsApp before retrying.')
    } finally { setBusy(false) }
  }
  return <div className="mt-4">
    <button type="button" disabled={busy} onClick={send} className="rounded-lg bg-forest px-4 py-2 text-sm font-bold text-white disabled:opacity-50">
      {busy ? 'Sending…' : 'Send test follow-up'}
    </button>
    <p className="mt-2 text-xs text-ink/60">Sends to your configured test phone. Message the Meta test number first, within the last 24 hours.</p>
    {result && <p role="status" className="mt-2 text-sm">{result}</p>}
  </div>
}
