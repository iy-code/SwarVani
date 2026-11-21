export type SessionState =
  | 'idle'
  | 'disclosure'
  | 'listening'
  | 'awaiting_confirmation'
  | 'saved'

export type FeverAgeGroup = 'under_two_months' | 'two_months_or_older' | 'unknown'

export type DangerSignInfo = {
  key: string
  labelEn: string
  labelHi: string
  actionHi: string
  matched: string
  confident: boolean
  level: 'check' | 'urgent'
  ageGroup?: FeverAgeGroup
  source: string
}

export type VisitRecord = {
  id: string
  beneficiaryName: string | null
  beneficiaryId: string | null
  visitType: string
  weightKg: number | null
  bp: string | null
  hb: number | null
  vaccination: string | null
  supplements: string[]
  nextVisit: string | null
  notes: string | null
  dangerSigns: DangerSignInfo[]
  followUps: string[]
  transcript: string
  corrections: { utterance: string; changed: string[] }[]
  createdAt: string
}

export type SessionMeta = {
  sessionId: string
  demo: boolean
  stt: string
  tts: string
  llm: string
  language: string
  source?: string
}

export type ServerEvent =
  | { type: 'hello'; sessionId: string; demo: boolean; speech: string; llm: string; language: string }
  | {
      type: 'session'
      sessionId: string
      demo: boolean
      stt: string
      tts: string
      llm: string
      language: string
      source: string
    }
  | { type: 'state'; state: SessionState }
  | { type: 'transcript'; text: string; final: boolean }
  | { type: 'agent'; text: string; speak?: boolean; kind?: string; audio?: string | null }
  | { type: 'danger'; signs: DangerSignInfo[]; message: string; source: string; audio?: string | null }
  | {
      type: 'record'
      record: VisitRecord
      missing: string[]
      extraction: string
      changed: string[]
    }
  | { type: 'saved'; record: VisitRecord; followUps: string[] }
  | { type: 'summary'; text: string; record: VisitRecord; audio?: string | null }
  | { type: 'error'; message: string }

export type Turn = {
  id: string
  role: 'agent' | 'asha'
  text: string
  kind?: string
}

export type FollowUp = {
  recordId: string
  beneficiaryName: string | null
  visitType: string
  nextVisit: string | null
  line: string
  danger: boolean
  checkUp?: boolean
  whatsappStatus: 'confirmed' | 'reschedule_requested' | 'no_response' | 'asha_callback_requested'
  whatsappLastReply: string | null
  whatsappUpdatedAt: string | null
}

export type WhatsAppStatus = FollowUp['whatsappStatus']

export type WhatsAppUpdate = {
  recordId: string
  beneficiaryName: string | null
  status: WhatsAppStatus
  reply: string | null
  createdAt: string
}

export type WhatsAppUpdatesPayload = {
  updates: WhatsAppUpdate[]
  summary: Record<WhatsAppStatus, number>
  webhookConfigured: boolean
  sendingConfigured: boolean
  simulatorEnabled: boolean
}
