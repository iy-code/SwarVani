import { useState } from 'react'
import { apiUrl } from '../lib/api'

const replies = [
  ['confirmed', 'Confirm'],
  ['reschedule_requested', 'Reschedule'],
  ['asha_callback_requested', 'Request ASHA call'],
] as const

export default function WhatsAppSimulator({ recordId, onReply }: { recordId: string; onReply: () => void }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function simulate(status: string, label: string) {
    setBusy(true)
    setMessage('')
    try {
      const response = await fetch(apiUrl(`/api/whatsapp/simulate/${encodeURIComponent(recordId)}?status=${status}`), {
        method: 'POST',
        headers: { 'X-SwarVani-Test': '1' },
      })
      if (!response.ok) {
        const body = await response.json()
        throw new Error(body.detail || 'Could not simulate the reply.')
      }
      setMessage(`Simulated reply recorded: ${label}`)
      onReply()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not simulate the reply.')
    } finally {
      setBusy(false)
    }
  }

  return <fieldset className="mt-4 rounded-lg border border-forest/15 p-3" disabled={busy}>
    <legend className="px-1 text-sm font-bold text-forest">Simulate WhatsApp reply</legend>
    <p className="mb-2 text-xs text-ink/60">Demo only. No WhatsApp message is sent.</p>
    <div className="flex flex-wrap gap-2">
      {replies.map(([status, label]) => <button key={status} type="button" onClick={() => void simulate(status, label)} className="rounded-lg bg-forest px-3 py-2 text-xs font-bold text-white disabled:opacity-50">{label}</button>)}
    </div>
    {message && <p role="status" className="mt-2 text-xs">{message}</p>}
  </fieldset>
}
