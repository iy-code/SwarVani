import type { DangerSignInfo, FeverAgeGroup } from '../lib/types'
import { AlertIcon } from './Icons'

type Props = {
  signs: DangerSignInfo[]
  message: string
  onAgeChange?: (ageGroup: FeverAgeGroup) => void
}

const AGE_OPTIONS = [
  ['under_two_months', 'Under 2 months', '2 महीने से कम'],
  ['two_months_or_older', '2 months or older', '2 महीने या उससे ज़्यादा'],
  ['unknown', 'Age unknown', 'उम्र पता नहीं'],
] as const

export default function DangerAlert({ signs, message, onAgeChange }: Props) {
  if (!signs.length) return null
  const urgent = signs.some((sign) => sign.level !== 'check')
  const fever = signs.find((sign) => sign.key === 'fever')

  return (
    <div className={`mt-4 overflow-hidden rounded-card border ${urgent ? 'border-danger/30 bg-danger-soft' : 'border-turmeric/45 bg-amber-50'}`}>
      <div className="flex items-start gap-3 border-b border-black/10 px-5 py-4" role={urgent ? 'alert' : 'status'}>
        <span className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl text-white ${urgent ? 'bg-danger' : 'bg-amber-800'}`}>
          <AlertIcon className="h-5 w-5" />
        </span>
        <div>
          <p className="text-sm font-bold text-ink">{urgent ? 'Urgent referral' : 'Immediate check-up'}</p>
          <p lang="hi" className="font-hindi text-xs font-semibold text-ink">{urgent ? 'तुरंत रेफ़रल' : 'अभी जाँच करें'}</p>
          <p className="mt-1 text-sm font-semibold leading-snug text-ink">{message}</p>
        </div>
      </div>

      {fever && <div className="border-b border-black/10 px-5 py-4">
        {onAgeChange ? <fieldset>
          <legend className="text-sm font-bold text-ink">How old is the patient? <span lang="hi" className="font-hindi">मरीज़ की उम्र क्या है?</span></legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {AGE_OPTIONS.map(([value, label, hindi]) => (
              <button key={value} type="button" onClick={() => onAgeChange(value)} aria-pressed={(fever.ageGroup ?? 'unknown') === value}
                className="min-h-12 rounded-lg border border-forest/30 bg-white px-3 py-1.5 text-left text-xs font-bold text-forest aria-pressed:border-forest aria-pressed:bg-forest aria-pressed:text-white">
                {label}<span lang="hi" className="block font-hindi">{hindi}</span>
              </button>
            ))}
          </div>
        </fieldset> : <p className="text-sm font-semibold text-ink">Age group: {AGE_OPTIONS.find(([value]) => value === fever.ageGroup)?.[1] ?? 'Age unknown'}</p>}
      </div>}

      <ul className="divide-y divide-black/10">
        {signs.map((sign) => (
          <li key={sign.key} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 py-3">
            <div>
              <p className="text-sm font-semibold text-ink">{sign.labelEn}</p>
              <p className="font-hindi text-xs text-ink/60">{sign.labelHi}</p>
            </div>
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
                sign.level === 'check' ? 'bg-turmeric/25 text-amber-900' : 'bg-danger/15 text-danger'
              }`}
            >
              {sign.level === 'check' ? 'Check now' : 'Urgent'}{sign.confident ? '' : ' · Verify phrase'}
            </span>
          </li>
        ))}
      </ul>

      <p className="border-t border-black/10 px-5 py-3 text-xs leading-relaxed text-ink/75">
        This prompt is based on words in the note. Confirm the findings with the ANM/PHC.
      </p>
    </div>
  )
}
