import { useState } from 'react'
import type { FormEvent } from 'react'
import type { WorkerProfile } from '../lib/auth'
import { BackIcon, CheckIcon, ShieldIcon, WaveIcon } from './Icons'

type Props = {
  onBack: () => void
  onSignIn: (profile: WorkerProfile) => void
}

export default function SignIn({ onBack, onSignIn }: Props) {
  const [name, setName] = useState('')
  const [workerId, setWorkerId] = useState('')
  const [district, setDistrict] = useState('')
  const [error, setError] = useState('')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const profile = { name: name.trim(), workerId: workerId.trim(), district: district.trim() }
    if (!profile.name || !profile.workerId || !profile.district) {
      setError('Enter your name, ASHA ID, and district to continue.')
      return
    }
    onSignIn(profile)
  }

  return (
    <main className="min-h-screen bg-paper text-ink lg:grid lg:grid-cols-[1.05fr_0.95fr]">
      <section className="relative hidden overflow-hidden bg-forest-deep px-12 py-10 text-cream lg:flex lg:flex-col">
        <div className="field-lines absolute inset-0 opacity-20" />
        <div className="relative flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center border border-cream/25 bg-cream/10">
            <WaveIcon className="h-5 w-5 text-turmeric" />
          </span>
          <div>
            <p className="font-hindi text-lg font-bold">स्वरवाणी</p>
            <p className="text-xs uppercase tracking-[0.24em] text-cream/55">Field register</p>
          </div>
        </div>

        <div className="relative my-auto max-w-2xl py-16">
          <p className="mb-7 flex items-center gap-3 text-sm font-bold uppercase tracking-[0.22em] text-turmeric">
            <span className="h-px w-10 bg-turmeric" /> Your field desk
          </p>
          <h1 className="font-display text-6xl leading-[0.98] tracking-[-0.035em] xl:text-7xl">
            Every visit,
            <br />
            <em className="font-normal text-mint">still in your voice.</em>
          </h1>
          <p className="mt-8 max-w-lg text-lg leading-8 text-cream/68">
            Sign in once on this device. Speak the visit, verify the record, and carry the next action forward.
          </p>
        </div>

        <div className="relative grid grid-cols-3 border-t border-cream/15 pt-6 text-sm text-cream/60">
          <span>Hindi + Hinglish</span>
          <span>Danger scan first</span>
          <span>Confirm before save</span>
        </div>
      </section>

      <section className="flex min-h-screen items-center px-5 py-8 sm:px-12 lg:px-16 xl:px-24">
        <div className="mx-auto w-full max-w-md">
          <button type="button" onClick={onBack} className="mb-12 inline-flex items-center gap-2 text-sm font-bold text-forest/70 hover:text-forest">
            <BackIcon className="h-4 w-4" /> Back to SwarVani
          </button>

          <div className="mb-9 lg:hidden">
            <p className="font-hindi text-2xl font-bold text-forest">स्वरवाणी</p>
            <p className="mt-1 text-sm text-ink/50">Voice field register</p>
          </div>

          <h2 className="font-display text-4xl leading-none tracking-tight text-forest">Open your visit register</h2>
          <p lang="hi" className="mt-3 font-hindi font-semibold text-forest">अपना रजिस्टर खोलें</p>
          <p className="mt-3 text-base leading-7 text-ink/75">Enter your details to continue on this device.</p>

          <form onSubmit={submit} className="mt-9 space-y-5">
            <label className="block">
              <span className="mb-2 block text-sm font-bold text-ink">Full name · <span lang="hi">पूरा नाम</span></span>
              <input
                autoComplete="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="w-full border border-forest/20 bg-cream/55 px-4 py-3.5 text-base outline-none transition focus:border-forest focus:bg-white"
                placeholder="e.g. Sunita Devi"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-bold text-ink">ASHA worker ID · <span lang="hi">आशा आईडी</span></span>
              <input
                autoComplete="username"
                value={workerId}
                onChange={(event) => setWorkerId(event.target.value)}
                className="w-full border border-forest/20 bg-cream/55 px-4 py-3.5 text-base outline-none transition focus:border-forest focus:bg-white"
                placeholder="e.g. ASHA-2048"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-bold text-ink">District or block · <span lang="hi">ज़िला या प्रखंड</span></span>
              <input
                autoComplete="address-level2"
                value={district}
                onChange={(event) => setDistrict(event.target.value)}
                className="w-full border border-forest/20 bg-cream/55 px-4 py-3.5 text-base outline-none transition focus:border-forest focus:bg-white"
                placeholder="e.g. Patna Rural"
              />
            </label>

            {error && <p role="alert" className="border-l-4 border-danger bg-danger-soft px-4 py-3 text-sm font-semibold text-danger">{error}</p>}

            <button type="submit" className="flex w-full items-center justify-center gap-2 bg-forest px-5 py-4 text-base font-bold text-cream transition hover:bg-forest-deep">
              <CheckIcon className="h-5 w-5" /> Continue · <span lang="hi">आगे बढ़ें</span>
            </button>
          </form>

          <div className="mt-7 flex gap-3 border-t border-forest/10 pt-6 text-sm leading-6 text-ink/50">
            <ShieldIcon className="mt-0.5 h-5 w-5 shrink-0 text-moss" />
            <p>Your worker profile is saved in this browser. Use sample visit details when trying the app.</p>
          </div>
        </div>
      </section>
    </main>
  )
}
