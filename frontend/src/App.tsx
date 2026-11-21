import { useEffect, useState } from 'react'
import DangerAlert from './components/DangerAlert'
import WhatsAppTest from './components/WhatsAppTest'
import WhatsAppSimulator from './components/WhatsAppSimulator'
import Landing from './components/Landing'
import SignIn from './components/SignIn'
import VisitForm from './components/VisitForm'
import { ArrowIcon, BackIcon, CheckIcon, HomeIcon, ListIcon, LogoutIcon, MicIcon, StopIcon, WaveIcon } from './components/Icons'
import { clearWorker, loadWorker, saveWorker, workerInitials } from './lib/auth'
import type { WorkerProfile } from './lib/auth'
import { useVoiceSession } from './lib/useVoiceSession'
import { apiUrl } from './lib/api'
import type { FollowUp, WhatsAppUpdate, WhatsAppUpdatesPayload } from './lib/types'

type Page = 'landing' | 'signin' | 'visit' | 'followups'

const SAMPLE_NOTE = 'Demo Sita ka ANC checkup kiya, weight 52 kilo, agle mahine 15 ko aana hai.'
const VISIT_STEP_LABELS: Record<string, string> = {
  listening: 'Ready for your note',
  awaiting_confirmation: 'Ready to review',
  saved: 'Saved',
}

const WHATSAPP_STATUS = {
  confirmed: { label: 'Confirmed', className: 'bg-mint text-forest' },
  reschedule_requested: { label: 'Reschedule requested', className: 'bg-turmeric/25 text-[#80520c]' },
  no_response: { label: 'No response', className: 'bg-cream text-ink/60' },
  asha_callback_requested: { label: 'ASHA callback requested', className: 'bg-danger-soft text-danger' },
} as const

export default function App() {
  const [page, setPage] = useState<Page>('landing')
  const [user, setUser] = useState<WorkerProfile | null>(() => loadWorker())
  const [pendingPage, setPendingPage] = useState<'visit' | 'followups'>('visit')
  const voice = useVoiceSession()
  const [correction, setCorrection] = useState('')
  const [followUps, setFollowUps] = useState<FollowUp[]>([])
  const [whatsappUpdates, setWhatsappUpdates] = useState<WhatsAppUpdate[]>([])
  const [whatsappSummary, setWhatsappSummary] = useState<WhatsAppUpdatesPayload['summary']>({
    confirmed: 0,
    reschedule_requested: 0,
    no_response: 0,
    asha_callback_requested: 0,
  })
  const [whatsappConfigured, setWhatsappConfigured] = useState(false)
  const [simulatorEnabled, setSimulatorEnabled] = useState(false)
  const [replyRevision, setReplyRevision] = useState(0)
  const [followUpError, setFollowUpError] = useState('')
  const [followUpsLoaded, setFollowUpsLoaded] = useState(false)

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [page])

  useEffect(() => {
    if (page === 'visit') {
      setCorrection('')
      voice.restart()
      void voice.connect()
    } else {
      voice.disconnect()
    }
    // Connection lifecycle follows the selected screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page])

  useEffect(() => {
    if (page !== 'followups') return
    let active = true
    const loadFollowUps = () => {
      Promise.all([fetch(apiUrl('/api/followups')), fetch(apiUrl('/api/whatsapp/updates'))])
        .then(async ([followUpResponse, whatsappResponse]) => {
          if (!followUpResponse.ok || !whatsappResponse.ok) throw new Error('Could not load follow-ups.')
          return Promise.all([
            followUpResponse.json() as Promise<{ followUps: FollowUp[] }>,
            whatsappResponse.json() as Promise<WhatsAppUpdatesPayload>,
          ])
        })
        .then(([followUpPayload, whatsappPayload]) => {
          if (!active) return
          setFollowUps(followUpPayload.followUps ?? [])
          setWhatsappUpdates(whatsappPayload.updates ?? [])
          setWhatsappSummary(whatsappPayload.summary)
          setWhatsappConfigured(whatsappPayload.webhookConfigured && whatsappPayload.sendingConfigured)
          setSimulatorEnabled(whatsappPayload.simulatorEnabled)
          setFollowUpError('')
          setFollowUpsLoaded(true)
        })
        .catch(() => { if (active) setFollowUpError('Follow-ups could not load. Check your connection and try again.') })
    }
    loadFollowUps()
    const refreshTimer = window.setInterval(loadFollowUps, 5000)
    return () => {
      active = false
      window.clearInterval(refreshTimer)
    }
  }, [page, replyRevision])

  const goBack = () => {
    voice.disconnect()
    setPage('landing')
  }

  const openProtected = (target: 'visit' | 'followups') => {
    if (user) {
      setFollowUpError('')
      setPage(target)
      return
    }
    setPendingPage(target)
    setPage('signin')
  }

  const signIn = (profile: WorkerProfile) => {
    saveWorker(profile)
    setUser(profile)
    setPage(pendingPage)
  }

  const signOut = () => {
    voice.disconnect()
    clearWorker()
    setUser(null)
    setPage('landing')
  }

  if (page === 'landing') {
    return <Landing user={user} onStart={() => openProtected('visit')} onRecords={() => openProtected('followups')} onSignIn={() => { setPendingPage('visit'); setPage('signin') }} onSignOut={signOut} />
  }

  if (page === 'signin') return <SignIn onBack={() => setPage('landing')} onSignIn={signIn} />

  if (page === 'followups') {
    return (
      <AppShell user={user!} active="followups" onHome={goBack} onVisit={() => setPage('visit')} onFollowups={() => setPage('followups')} onSignOut={signOut}>
        <section className="mx-auto max-w-6xl px-5 py-10 lg:px-10">
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-moss">Visit queue</p>
          <h1 className="mt-2 font-display text-5xl leading-none text-forest">Follow-up board</h1>
          <p lang="hi" className="mt-2 font-hindi font-semibold text-forest">अगली मुलाक़ातें</p>
          <p className="mt-2 text-base text-ink/75">See what to do at the next visit and check for replies.</p>
          <div className="mt-7 grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <div>
              {followUpError && <div className="rounded-xl bg-danger-soft p-4 text-sm text-danger"><p role="alert">{followUpError}</p><button type="button" className="min-h-11 font-bold underline" onClick={() => setReplyRevision((revision) => revision + 1)}>Try again</button></div>}
              {!followUpError && !followUpsLoaded && <p role="status">Loading follow-ups…</p>}
              {!followUpError && followUpsLoaded && followUps.length === 0 && <div className="rounded-card border border-forest/10 bg-paper p-6"><p>No follow-ups yet. Start a visit and save it to see the next steps here.</p><button type="button" className="mt-3 min-h-11 rounded-xl bg-forest px-4 font-bold text-white" onClick={() => setPage('visit')}>Start a visit</button></div>}
              <ul className="grid gap-3">
                {followUps.map((item, i) => (
                  <li key={`${item.recordId}-${i}`} className={`border-l-4 bg-paper p-5 shadow-sm ${item.danger ? 'border-danger' : item.checkUp ? 'border-turmeric' : 'border-moss'}`}>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div><p className="font-bold">{item.beneficiaryName || 'Beneficiary'}</p><p className="mt-1 text-xs uppercase tracking-wide text-ink/50">{item.visitType} · {item.recordId}</p></div>
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        <span className={`rounded-full px-3 py-1 text-xs font-bold ${WHATSAPP_STATUS[item.whatsappStatus].className}`}>
                          WhatsApp · {WHATSAPP_STATUS[item.whatsappStatus].label}
                        </span>
                        {item.nextVisit && <time className="rounded-full bg-cream px-3 py-1 text-xs font-semibold">{item.nextVisit}</time>}
                      </div>
                    </div>
                    <p className="mt-4 text-sm text-ink/75">{item.line}</p>
                    {followUps.findIndex((row) => row.recordId === item.recordId) === i && (
                      <>
                        {simulatorEnabled && <WhatsAppSimulator recordId={item.recordId} onReply={() => setReplyRevision((revision) => revision + 1)} />}
                        {whatsappConfigured && <WhatsAppTest recordId={item.recordId} />}
                      </>
                    )}
                    {item.whatsappLastReply && <p className="mt-2 text-xs text-ink/50">Latest reply: {item.whatsappLastReply}</p>}
                    {item.danger && <p className="mt-2 text-xs font-bold text-danger">Urgent referral flag</p>}
                    {item.checkUp && <p className="mt-2 text-xs font-bold text-amber-900">Immediate check-up</p>}
                  </li>
                ))}
              </ul>
            </div>

            <aside className="h-fit border border-forest/10 bg-paper p-5 shadow-sm lg:sticky lg:top-6">
              <div className="flex items-start justify-between gap-3">
                <div><p className="text-xs font-bold uppercase tracking-[0.16em] text-moss">WhatsApp updates</p><h2 className="mt-1 font-display text-2xl text-forest">Reply activity</h2></div>
                <span className={`mt-1 h-2.5 w-2.5 rounded-full ${whatsappConfigured ? 'bg-[#25d366]' : 'bg-turmeric'}`} title={whatsappConfigured ? 'Connected' : 'Credentials required'} />
              </div>
              <div className="mt-5 grid grid-cols-2 gap-2">
                {(Object.keys(WHATSAPP_STATUS) as Array<keyof typeof WHATSAPP_STATUS>).map((status) => (
                  <div key={status} className="rounded-xl bg-cream p-3">
                    <p className="text-xl font-extrabold text-forest">{whatsappSummary[status]}</p>
                    <p className="mt-1 text-[10px] font-bold leading-tight text-ink/50">{WHATSAPP_STATUS[status].label}</p>
                  </div>
                ))}
              </div>
              <div className="mt-5 border-t border-forest/10 pt-4">
                {whatsappUpdates.length === 0 ? (
                  <p className="text-xs leading-relaxed text-ink/50">New beneficiary replies will appear here automatically.</p>
                ) : (
                  <ul className="space-y-4">
                    {whatsappUpdates.slice(0, 8).map((update) => (
                      <li key={`${update.recordId}-${update.createdAt}`}>
                        <p className="text-sm font-bold text-forest">{update.beneficiaryName || update.recordId}</p>
                        <p className="mt-1 text-xs text-ink/60">{WHATSAPP_STATUS[update.status].label}{update.reply ? ` · “${update.reply}”` : ''}</p>
                        <time className="mt-1 block text-[10px] text-ink/40">{new Date(update.createdAt).toLocaleString()}</time>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {!whatsappConfigured && <p className="mt-5 rounded-xl bg-turmeric/15 p-3 text-sm leading-relaxed text-[#80520c]">{simulatorEnabled ? 'Demo mode. Use the reply buttons in a saved visit to see how this list updates.' : 'Live WhatsApp replies are unavailable.'}</p>}
            </aside>
          </div>
        </section>
      </AppShell>
    )
  }

  return (
    <AppShell user={user!} active="visit" onHome={goBack} onVisit={() => setPage('visit')} onFollowups={() => setPage('followups')} onSignOut={signOut}>
      <header className="visit-banner px-5 text-white lg:px-10">
        <div className="mx-auto flex min-h-40 max-w-6xl flex-col items-start justify-center gap-3 py-5 lg:min-h-52">
          <div><h1 className="max-w-52 font-display text-[2.1rem] leading-none sm:max-w-none sm:text-5xl">Record a visit</h1><p lang="hi" className="mt-2 font-hindi font-semibold text-cream">मुलाक़ात दर्ज करें</p></div>
          <span role="status" className="rounded-full bg-cream px-3 py-1 text-xs font-semibold text-forest">{voice.status === 'live' ? 'Connected' : 'Session: ' + voice.status}</span>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-4 py-6 sm:px-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <section className="space-y-5">
          <div className="rounded-card border border-forest/15 bg-paper p-4 text-ink shadow-sm sm:p-6">
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-lg font-bold">Visit conversation</h2>
              <span className="rounded-full bg-cream px-3 py-1 text-xs font-semibold text-forest">{VISIT_STEP_LABELS[voice.sessionState] ?? 'Getting ready'}</span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-ink/75">Speak or type in Hindi, Hinglish, or English. Include the name, visit type, and next visit date if you know it.</p>
            <div className="mt-4 max-h-[22rem] space-y-3 overflow-y-auto" aria-live="polite">
              {voice.turns.length === 0 && <p className="text-sm text-ink/70">Connecting to the visit assistant…</p>}
              {voice.turns.map((turn) => (
                <p key={turn.id} className={`max-w-[94%] rounded-2xl px-4 py-3 text-base leading-relaxed ${turn.role === 'asha' ? 'ml-auto bg-moss text-white' : turn.kind === 'danger' ? 'bg-danger-soft text-danger' : 'bg-cream text-ink'}`}>
                  {turn.text}
                </p>
              ))}
              {voice.interim && <p className="rounded-xl bg-cream/70 p-3 text-lg italic text-ink/70">{voice.interim}</p>}
            </div>
            <DangerAlert signs={voice.danger} message={voice.dangerMessage} onAgeChange={voice.status === 'live' && voice.sessionState !== 'saved' ? voice.setFeverAge : undefined} />
            {voice.error && <p role="alert" className="mt-4 rounded-xl bg-danger-soft p-3 text-sm text-danger">{voice.error}</p>}
            <div className="mt-5 flex flex-wrap gap-3">
              {!voice.listening ? (
                <button disabled={!['listening', 'awaiting_confirmation'].includes(voice.sessionState) || voice.status !== 'live'} onClick={() => void voice.startListening()} className="inline-flex min-h-12 items-center gap-3 rounded-xl bg-forest px-5 py-3 text-left text-sm font-bold text-cream disabled:cursor-not-allowed disabled:opacity-45" type="button"><MicIcon className="h-5 w-5" /><span>{voice.sessionState === 'awaiting_confirmation' ? 'Speak a correction' : 'Start speaking'} <span lang="hi" className="block font-hindi text-xs">{voice.sessionState === 'awaiting_confirmation' ? 'सुधार बोलें' : 'बोलना शुरू करें'}</span></span></button>
              ) : (
                <button onClick={voice.stopListening} className="inline-flex items-center gap-2 rounded-full bg-danger px-5 py-3 text-sm font-semibold text-white" type="button"><StopIcon className="h-4 w-4" /> Stop listening</button>
              )}
              {voice.sessionState === 'awaiting_confirmation' && <a href="#visit-review" className="inline-flex min-h-11 items-center rounded-full border border-forest/30 px-4 text-sm font-bold text-forest">Review record</a>}
            </div>
            <form className="mt-5" onSubmit={(event) => { event.preventDefault(); if (correction.trim()) { voice.submitText(correction, voice.record ? 'correct' : 'utterance'); setCorrection('') } }}>
              <label htmlFor="visit-note" className="mb-2 block text-sm font-bold">{voice.record ? 'Type a correction' : 'Or type your visit note'}</label>
              <div className="flex items-end gap-2">
                <textarea id="visit-note" rows={2} value={correction} onChange={(event) => setCorrection(event.target.value)} className="w-0 min-w-0 flex-1 rounded-xl border border-forest/30 bg-white px-3 py-2 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-forest" placeholder={voice.record ? 'Example: Name Sahil, next visit tomorrow' : 'Name, visit type, and what happened'} />
                <button className="inline-flex min-h-12 shrink-0 items-center gap-2 rounded-xl bg-forest px-3 text-sm font-bold text-cream disabled:opacity-50" disabled={!correction.trim() || voice.status !== 'live' || !['listening', 'awaiting_confirmation'].includes(voice.sessionState)} type="submit">Send <ArrowIcon className="h-4 w-4" /></button>
              </div>
            </form>
            {!voice.record && !correction && voice.meta?.stt === 'browser' && <button type="button" onClick={() => { setCorrection(SAMPLE_NOTE); document.getElementById('visit-note')?.focus() }} className="mt-1 min-h-11 text-sm font-semibold text-forest underline underline-offset-4">Use a sample note</button>}
          </div>
          {voice.sessionState === 'saved' && <div className="rounded-card border border-moss/20 bg-mint/40 p-5"><p className="font-bold text-forest">Visit saved</p><ul className="mt-2 list-inside list-disc text-sm text-forest/80">{voice.savedFollowUps.map((line) => <li key={line}>{line}</li>)}</ul><div className="mt-2 flex flex-wrap gap-x-5"><button type="button" onClick={() => { setCorrection(''); voice.restart() }} className="min-h-11 text-sm font-bold text-forest underline underline-offset-4">Record another visit</button><button type="button" onClick={() => setPage('followups')} className="min-h-11 text-sm font-bold text-forest underline underline-offset-4">Open follow-ups</button></div></div>}
        </section>
        <aside id="visit-review" className="scroll-mt-4 space-y-5">
          <VisitForm record={voice.record} missing={voice.missing} />
          {voice.sessionState === 'awaiting_confirmation' && <button onClick={() => void voice.confirm()} className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-forest px-5 py-3 text-sm font-bold text-white" type="button"><CheckIcon className="h-4 w-4" /> Confirm and save</button>}
          <div className="rounded-card border border-forest/10 bg-paper p-5">
            <p className="text-sm font-bold uppercase tracking-[0.16em] text-moss">Safety boundary</p>
            <p className="mt-2 text-sm leading-relaxed text-ink/75">The app highlights symptoms to check now and clear signs that may need urgent referral. Confirm findings with your ANM/PHC.</p>
          </div>
        </aside>
      </div>
    </AppShell>
  )
}

type ShellProps = {
  user: WorkerProfile
  active: 'visit' | 'followups'
  onHome: () => void
  onVisit: () => void
  onFollowups: () => void
  onSignOut: () => void
  children: React.ReactNode
}

function AppShell({ user, active, onHome, onVisit, onFollowups, onSignOut, children }: ShellProps) {
  const nav = 'flex items-center gap-3 px-4 py-3 text-sm font-bold transition'
  return (
    <main className="min-h-screen bg-cream lg:grid lg:grid-cols-[15.5rem_1fr]">
      <aside className="hidden min-h-screen flex-col bg-forest-deep text-cream lg:flex">
        <button onClick={onHome} type="button" className="flex items-center gap-3 border-b border-cream/10 px-6 py-6 text-left">
          <span className="grid h-10 w-10 place-items-center border border-cream/20 text-turmeric"><WaveIcon className="h-5 w-5" /></span>
          <span><span className="block font-hindi text-lg font-bold">स्वरवाणी</span><span className="block text-[10px] uppercase tracking-[0.2em] text-cream/45">Field register</span></span>
        </button>
        <nav className="space-y-1 px-3 py-6">
          <button onClick={onHome} type="button" className={`${nav} w-full text-cream/65 hover:bg-cream/8 hover:text-cream`}><HomeIcon className="h-5 w-5" /> Home</button>
          <button onClick={onVisit} type="button" className={`${nav} w-full ${active === 'visit' ? 'bg-turmeric text-forest' : 'text-cream/65 hover:bg-cream/8 hover:text-cream'}`}><MicIcon className="h-5 w-5" /> New visit</button>
          <button onClick={onFollowups} type="button" className={`${nav} w-full ${active === 'followups' ? 'bg-turmeric text-forest' : 'text-cream/65 hover:bg-cream/8 hover:text-cream'}`}><ListIcon className="h-5 w-5" /> Follow-ups</button>
        </nav>
        <div className="mt-auto border-t border-cream/10 p-5">
          <div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center bg-mint text-xs font-extrabold text-forest">{workerInitials(user.name)}</span><span className="min-w-0"><span className="block truncate text-sm font-bold">{user.name}</span><span className="block truncate text-xs text-cream/45">{user.district}</span></span></div>
          <button onClick={onSignOut} type="button" className="mt-4 flex items-center gap-2 text-xs font-bold text-cream/50 hover:text-cream"><LogoutIcon className="h-4 w-4" /> Sign out</button>
        </div>
      </aside>
      <section className="min-w-0">
        <header className="border-b border-forest/10 bg-paper px-4 py-2 lg:hidden">
          <div className="flex items-center justify-between"><button onClick={onHome} type="button" className="min-h-11 font-hindi text-lg font-bold text-forest">स्वरवाणी</button><button onClick={onSignOut} type="button" className="min-h-11 px-2 text-xs font-bold text-danger">Sign out</button></div>
          <nav aria-label="Workspace" className="grid grid-cols-2 gap-2">
            <button onClick={onVisit} type="button" aria-current={active === 'visit' ? 'page' : undefined} className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-forest/15 px-2 text-sm font-bold text-forest aria-[current=page]:bg-mint"><MicIcon className="h-4 w-4" /> Visit</button>
            <button onClick={onFollowups} type="button" aria-current={active === 'followups' ? 'page' : undefined} className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-forest/15 px-2 text-sm font-bold text-forest aria-[current=page]:bg-mint"><ListIcon className="h-4 w-4" /> Follow-ups</button>
          </nav>
        </header>
        {children}
      </section>
    </main>
  )
}
