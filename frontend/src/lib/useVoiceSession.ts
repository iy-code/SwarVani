import { useCallback, useEffect, useRef, useState } from 'react'
import type { DangerSignInfo, ServerEvent, SessionMeta, SessionState, Turn, VisitRecord } from './types'
import { voiceSocketUrl } from './api'

// ---------------------------------------------------------------------------
// Browser speech (demo mode). In production the mic audio streams to Gnani
// Prisma over the backend WebSocket; the protocol is identical from here on.
// ---------------------------------------------------------------------------

type SpeechResultLike = {
  resultIndex: number
  results: { isFinal: boolean; 0: { transcript: string }; length: number }[] & { length: number }
}

type SpeechRecognitionLike = {
  lang: string
  continuous: boolean
  interimResults: boolean
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((event: SpeechResultLike) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike

type QueuedSpeech = {
  text: string
  audio?: string | null
}

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

function looksLikeConfirmation(text: string): boolean {
  return /^(haan|han|ha|yes|sahi hai|theek hai|thik hai|correct|ok|okay|हाँ|हां)[.!\s]*$/i.test(text.trim())
}

export function useVoiceSession() {
  const [status, setStatus] = useState<'idle' | 'connecting' | 'live' | 'closed' | 'error'>('idle')
  const [sessionState, setSessionState] = useState<SessionState>('idle')
  const [meta, setMeta] = useState<SessionMeta | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [record, setRecord] = useState<VisitRecord | null>(null)
  const [missing, setMissing] = useState<string[]>([])
  const [danger, setDanger] = useState<DangerSignInfo[]>([])
  const [dangerMessage, setDangerMessage] = useState('')
  const [savedFollowUps, setSavedFollowUps] = useState<string[]>([])
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [speaking, setSpeaking] = useState(false)
  const [muted, setMuted] = useState(false)
  const [error, setError] = useState('')
  const [extractionSource, setExtractionSource] = useState('rules')

  const wsRef = useRef<WebSocket | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const micWanted = useRef(false)
  const speakingRef = useRef(false)
  const mutedRef = useRef(false)
  const turnSeq = useRef(0)
  const metaRef = useRef<SessionMeta | null>(null)
  const micStreamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const mutedGainRef = useRef<GainNode | null>(null)
  const gnaniMicRef = useRef(false)
  const sessionReadyRef = useRef(false)
  const audioPlayerRef = useRef<HTMLAudioElement | null>(null)
  const sessionStateRef = useRef<SessionState>('idle')
  const speechQueueRef = useRef<QueuedSpeech[]>([])
  const speechGenerationRef = useRef(0)
  const playNextSpeechRef = useRef<() => void>(() => undefined)
  const reconnectTimerRef = useRef<number | null>(null)
  const shouldReconnectRef = useRef(false)
  const connectRef = useRef<() => Promise<boolean>>(async () => false)

  const releaseMicrophone = useCallback(() => {
    micWanted.current = false
    gnaniMicRef.current = false
    recognitionRef.current?.abort()
    recognitionRef.current = null
    processorRef.current?.disconnect()
    mutedGainRef.current?.disconnect()
    micStreamRef.current?.getTracks().forEach((track) => track.stop())
    void audioContextRef.current?.close()
    processorRef.current = null
    mutedGainRef.current = null
    micStreamRef.current = null
    audioContextRef.current = null
    setListening(false)
  }, [])

  const addTurn = useCallback((role: Turn['role'], text: string, kind?: string) => {
    turnSeq.current += 1
    setTurns((prev) => [...prev, { id: `t${turnSeq.current}`, role, text, kind }])
  }, [])

  const resumeMicrophoneAfterSpeech = useCallback(() => {
    if (!micWanted.current) return
    if (gnaniMicRef.current) {
      setListening(true)
      return
    }
    if (recognitionRef.current) {
      try {
        recognitionRef.current.start()
        setListening(true)
      } catch {
        /* already running */
      }
    }
  }, [])

  const playNextSpeech = useCallback(() => {
    if (speakingRef.current) return
    const next = speechQueueRef.current.shift()
    if (!next) {
      setSpeaking(false)
      resumeMicrophoneAfterSpeech()
      return
    }

    speakingRef.current = true
    setSpeaking(true)
    setListening(false)
    try {
      recognitionRef.current?.stop()
    } catch {
      /* already stopped */
    }

    const generation = speechGenerationRef.current
    let finished = false
    const finish = () => {
      if (finished || generation !== speechGenerationRef.current) return
      finished = true
      audioPlayerRef.current = null
      speakingRef.current = false
      playNextSpeechRef.current()
    }

    if (next.audio) {
      const player = new Audio(next.audio)
      audioPlayerRef.current = player
      player.onended = finish
      player.onerror = finish
      void player.play().catch(finish)
      return
    }
    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(next.text)
      utterance.lang = metaRef.current?.language ?? 'hi-IN'
      utterance.rate = 0.98
      const languagePrefix = (metaRef.current?.language ?? 'hi-IN').slice(0, 2).toLowerCase()
      const matchingVoice = window.speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith(languagePrefix))
      if (matchingVoice) utterance.voice = matchingVoice
      utterance.onend = finish
      utterance.onerror = finish
      window.speechSynthesis.speak(utterance)
      return
    }
    finish()
  }, [resumeMicrophoneAfterSpeech])
  playNextSpeechRef.current = playNextSpeech

  const clearSpeechQueue = useCallback(() => {
    speechGenerationRef.current += 1
    speechQueueRef.current = []
    window.speechSynthesis?.cancel()
    audioPlayerRef.current?.pause()
    audioPlayerRef.current = null
    speakingRef.current = false
    setSpeaking(false)
  }, [])

  const speak = useCallback((text: string, audio?: string | null) => {
    // SwarVani accepts microphone input, but assistant responses are always
    // rendered as text. Keep this no-op as a final client-side guard even if
    // an older backend sends a speak/audio field.
    void text
    void audio
  }, [])

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current
    mutedRef.current = next
    setMuted(next)
    if (next) {
      clearSpeechQueue()
      resumeMicrophoneAfterSpeech()
    }
  }, [clearSpeechQueue, resumeMicrophoneAfterSpeech])

  const sendRaw = useCallback((payload: Record<string, unknown>) => {
    const socket = wsRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload))
      return true
    }
    setError('Not connected to the voice service. Is the backend running?')
    return false
  }, [])

  const applyEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case 'hello':
        case 'session':
          if (event.type === 'session') {
            sessionReadyRef.current = true
            setStatus('live')
            setError('')
          }
          setMeta((prev) => {
            const next = {
              sessionId: event.sessionId,
              demo: event.demo,
              stt: event.type === 'session' ? event.stt : (event.speech ?? 'browser'),
              tts: event.type === 'session' ? event.tts : 'browser',
              llm: event.type === 'session' ? event.llm : (event.llm ?? 'rules'),
              language: event.language,
              source: event.type === 'session' ? event.source : prev?.source,
            }
            metaRef.current = next
            return next
          })
          break
        case 'transcript':
          if (event.final) {
            setInterim('')
            addTurn('asha', event.text)
          } else setInterim(event.text)
          break
        case 'state':
          sessionStateRef.current = event.state
          setSessionState(event.state)
          break
        case 'agent':
          addTurn('agent', event.text, event.kind)
          if (event.speak) speak(event.text, event.audio)
          break
        case 'danger':
          setDanger(event.signs)
          setDangerMessage(event.message)
          if (event.audio !== undefined) speak(event.message, event.audio)
          else speak(event.message)
          break
        case 'record':
          setRecord(event.record)
          setMissing(event.missing)
          setDanger(event.record.dangerSigns)
          setExtractionSource(event.extraction)
          break
        case 'saved':
          releaseMicrophone()
          sessionStateRef.current = 'saved'
          setRecord(event.record)
          setDanger(event.record.dangerSigns)
          setSavedFollowUps(event.followUps)
          setSessionState('saved')
          break
        case 'summary':
          addTurn('agent', event.text, 'summary')
          if (event.audio !== undefined) speak(event.text, event.audio)
          else speak(event.text)
          break
        case 'error':
          setError(event.message)
          // A failed upstream STT request must not leave the UI in a fake
          // listening state with a live local microphone and no transcript.
          if (gnaniMicRef.current) {
            micWanted.current = false
            gnaniMicRef.current = false
            setListening(false)
            processorRef.current?.disconnect()
            mutedGainRef.current?.disconnect()
            micStreamRef.current?.getTracks().forEach((track) => track.stop())
            void audioContextRef.current?.close()
            processorRef.current = null
            mutedGainRef.current = null
            micStreamRef.current = null
            audioContextRef.current = null
          }
          break
      }
    },
    [addTurn, speak, releaseMicrophone],
  )

  const connect = useCallback(
    () =>
      new Promise<boolean>((resolve) => {
        shouldReconnectRef.current = true
        if (reconnectTimerRef.current !== null) {
          window.clearTimeout(reconnectTimerRef.current)
          reconnectTimerRef.current = null
        }
        if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
          if (sessionReadyRef.current) resolve(true)
          else {
            setError('The voice session is still starting. Try again in a moment.')
            resolve(false)
          }
          return
        }
        setStatus('connecting')
        sessionReadyRef.current = false
        setError('')
        let resolved = false
        let socket: WebSocket
        try {
          socket = new WebSocket(voiceSocketUrl())
        } catch {
          setStatus('error')
          setError('Could not open the voice socket.')
          resolve(false)
          return
        }
        wsRef.current = socket
        socket.onopen = () => {
          setStatus('live')
          socket.send(JSON.stringify({ type: 'start' }))
        }
        socket.onmessage = (message) => {
          try {
            const event = JSON.parse(message.data) as ServerEvent
            applyEvent(event)
            if (event.type === 'session' && !resolved) {
              sessionReadyRef.current = true
              resolved = true
              resolve(true)
            } else if (event.type === 'error' && !resolved) {
              resolved = true
              resolve(false)
              socket.close()
            }
          } catch {
            /* ignore malformed frame */
          }
        }
        socket.onerror = () => {
          setStatus('error')
          setError('Voice service is reconnecting. Start the backend on port 8000 if this message continues.')
          if (!resolved) {
            resolved = true
            resolve(false)
          }
        }
        socket.onclose = () => {
          if (wsRef.current !== socket) return
          wsRef.current = null
          sessionReadyRef.current = false
          releaseMicrophone()
          setStatus(shouldReconnectRef.current ? 'connecting' : 'closed')
          micWanted.current = false
          setListening(false)
          if (shouldReconnectRef.current && reconnectTimerRef.current === null) {
            reconnectTimerRef.current = window.setTimeout(() => {
              reconnectTimerRef.current = null
              void connectRef.current()
            }, 1500)
          }
          if (!resolved) {
            resolved = true
            resolve(false)
          }
        }
      }),
    [applyEvent, releaseMicrophone],
  )
  connectRef.current = connect

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    clearSpeechQueue()
    gnaniMicRef.current = false
    micWanted.current = false
    sessionReadyRef.current = false
    try {
      recognitionRef.current?.abort()
    } catch {
      /* noop */
    }
    recognitionRef.current = null
    processorRef.current?.disconnect()
    mutedGainRef.current?.disconnect()
    micStreamRef.current?.getTracks().forEach((track) => track.stop())
    void audioContextRef.current?.close()
    processorRef.current = null
    mutedGainRef.current = null
    micStreamRef.current = null
    audioContextRef.current = null
    wsRef.current?.close()
    wsRef.current = null
    setListening(false)
  }, [clearSpeechQueue])

  const submitText = useCallback(
    (text: string, kind: 'utterance' | 'correct' = 'utterance') => {
      const clean = text.trim()
      if (!clean) return
      addTurn('asha', clean)
      setInterim('')
      sendRaw({ type: kind, text: clean })
    },
    [addTurn, sendRaw],
  )

  const stopListening = useCallback(() => {
    micWanted.current = false
    setListening(false)
    setInterim('')
    if (gnaniMicRef.current) sendRaw({ type: 'audio_stop' })
    gnaniMicRef.current = false
    processorRef.current?.disconnect()
    mutedGainRef.current?.disconnect()
    micStreamRef.current?.getTracks().forEach((track) => track.stop())
    void audioContextRef.current?.close()
    processorRef.current = null
    mutedGainRef.current = null
    micStreamRef.current = null
    audioContextRef.current = null
    try {
      recognitionRef.current?.stop()
    } catch {
      /* noop */
    }
  }, [sendRaw])

  const startListening = useCallback(async () => {
    const ok = await connect()
    if (!ok) return
    clearSpeechQueue()

    if (metaRef.current?.stt === 'gnani-prisma') {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        })
        micStreamRef.current = stream
        const context = new AudioContext({ sampleRate: 16000 })
        audioContextRef.current = context
        await context.resume()
        sendRaw({ type: 'audio_start' })
        const source = context.createMediaStreamSource(stream)
        const processor = context.createScriptProcessor(512, 1, 1)
        const muted = context.createGain()
        muted.gain.value = 0
        const pendingSamples: number[] = []
        let resamplePosition = 0
        processor.onaudioprocess = (event) => {
          // Do not send the assistant's own TTS back into Prisma. Without this
          // half-duplex gate, the bot can transcribe its read-back as a new
          // visit note or even hear its own confirmation prompt.
          if (speakingRef.current) {
            pendingSamples.length = 0
            resamplePosition = 0
            return
          }
          const input = event.inputBuffer.getChannelData(0)
          const ratio = context.sampleRate / 16000
          while (resamplePosition < input.length) {
            const leftIndex = Math.floor(resamplePosition)
            const fraction = resamplePosition - leftIndex
            const rightIndex = Math.min(leftIndex + 1, input.length - 1)
            const sample = input[leftIndex] * (1 - fraction) + input[rightIndex] * fraction
            pendingSamples.push(Math.round(Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767)))
            resamplePosition += ratio
          }
          resamplePosition -= input.length
          const socket = wsRef.current
          if (socket?.readyState === WebSocket.OPEN) {
            while (pendingSamples.length >= 512) {
              const frame = new Int16Array(pendingSamples.splice(0, 512))
              socket.send(frame.buffer)
            }
          }
        }
        source.connect(processor)
        processor.connect(muted)
        muted.connect(context.destination)
        micStreamRef.current = stream
        audioContextRef.current = context
        processorRef.current = processor
        mutedGainRef.current = muted
        gnaniMicRef.current = true
        micWanted.current = true
        setListening(true)
        setError('')
      } catch {
        releaseMicrophone()
        setError('Could not start live audio. Check microphone permission and secure connection (HTTPS).')
      }
      return
    }

    const Ctor = getRecognitionCtor()
    if (!Ctor) {
      setError('This browser has no speech recognition. Type the note instead — the pipeline is the same.')
      return
    }

    const recognition = new Ctor()
    recognition.lang = metaRef.current?.language ?? 'hi-IN'
    recognition.continuous = true
    recognition.interimResults = true
    recognition.onresult = (event) => {
      if (speakingRef.current) return
      let finalText = ''
      let interimText = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        const chunk = result[0]?.transcript ?? ''
        if (result.isFinal) finalText += chunk
        else interimText += chunk
      }
      setInterim(interimText)
      if (finalText.trim()) {
        const kind = sessionStateRef.current === 'awaiting_confirmation' && !looksLikeConfirmation(finalText) ? 'correct' : 'utterance'
        submitText(finalText.trim(), kind)
      }
    }
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        setError('Microphone permission was blocked. Allow the mic or type the note instead.')
        micWanted.current = false
        setListening(false)
      } else if (event.error === 'no-speech') {
        // normal in a quiet room; keep listening
      } else if (event.error === 'network') {
        setError('Speech recognition needs a network connection in this browser.')
      }
    }
    recognition.onend = () => {
      setListening(false)
      if (micWanted.current && !speakingRef.current) {
        try {
          recognition.start()
          setListening(true)
        } catch {
          /* give up quietly */
        }
      }
    }
    recognitionRef.current = recognition
    micWanted.current = true
    try {
      recognition.start()
      setListening(true)
      setError('')
    } catch {
      setError('Could not start the microphone.')
    }
  }, [connect, sendRaw, submitText, releaseMicrophone, clearSpeechQueue])

  const confirm = useCallback(async () => {
    const ok = await connect()
    if (!ok) return
    sendRaw({ type: 'confirm' })
  }, [connect, sendRaw])

  const requestSummary = useCallback(async () => {
    const ok = await connect()
    if (!ok) return
    sendRaw({ type: 'summary' })
  }, [connect, sendRaw])

  const restart = useCallback(() => {
    clearSpeechQueue()
    setTurns([])
    setRecord(null)
    setMissing([])
    setDanger([])
    setDangerMessage('')
    setSavedFollowUps([])
    setSessionState('idle')
    sessionStateRef.current = 'idle'
    setError('')
    setInterim('')
    setExtractionSource('rules')
    sendRaw({ type: 'reset' })
  }, [sendRaw, clearSpeechQueue])

  useEffect(() => () => disconnect(), [disconnect])

  return {
    status,
    sessionState,
    meta,
    turns,
    record,
    missing,
    danger,
    dangerMessage,
    savedFollowUps,
    listening,
    interim,
    speaking,
    muted,
    error,
    extractionSource,
    setError,
    connect,
    disconnect,
    startListening,
    stopListening,
    toggleMute,
    submitText,
    setFeverAge: (ageGroup: import('./types').FeverAgeGroup) => sendRaw({ type: 'fever_age', ageGroup }),
    confirm,
    requestSummary,
    restart,
    hasSpeechRecognition: Boolean(getRecognitionCtor()),
  }
}
