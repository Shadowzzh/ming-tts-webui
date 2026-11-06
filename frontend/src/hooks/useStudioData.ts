import { useCallback, useEffect, useRef, useState } from "react"
import { api, message } from "@/lib/api"
import { isActive } from "@/lib/format"
import type { Job, Options, ServiceStatus, Voice } from "@/types"
type Resource = "options" | "status" | "voices" | "jobs"
export function useStudioData() {
  const [options, setOptions] = useState<Options | null>(null)
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [voices, setVoices] = useState<Voice[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [errors, setErrors] = useState<Partial<Record<Resource, string>>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const mounted = useRef(true)
  const inFlight = useRef(false)
  const optionsRef = useRef<Options | null>(null)
  const refresh = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    if (mounted.current) setRefreshing(true)
    const load = async <T,>(resource: Resource, fn: () => Promise<T>, save: (value: T) => void) => {
      try { const result = await fn(); if (mounted.current) { save(result); setErrors(old => ({ ...old, [resource]: undefined })) } }
      catch (error) { if (mounted.current) setErrors(old => ({ ...old, [resource]: message(error) })) }
    }
    await Promise.allSettled([
      ...(!optionsRef.current ? [load("options", api.options, value => { optionsRef.current = value; setOptions(value) })] : []),
      load("status", api.status, setStatus),
      load("voices", api.voices, value => setVoices(value.items)),
      load("jobs", api.jobs, value => setJobs(value.items)),
    ])
    inFlight.current = false
    if (mounted.current) { setLoading(false); setRefreshing(false) }
  }, [])
  useEffect(() => {
    mounted.current = true
    void refresh()
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void refresh() }, 5000)
    const onOnline = () => { void refresh() }
    window.addEventListener("online", onOnline)
    document.addEventListener("visibilitychange", onOnline)
    return () => { mounted.current = false; window.clearInterval(interval); window.removeEventListener("online", onOnline); document.removeEventListener("visibilitychange", onOnline) }
  }, [refresh])
  return { options, status, voices, jobs, errors, loading, refreshing, refresh }
}
export function useActiveJob(jobId: string | null, knownJobs: Job[], onMissing: () => void) {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState("")
  const missing = useRef(onMissing)
  missing.current = onMissing
  useEffect(() => {
    if (!jobId) { setJob(null); setError(""); return }
    let disposed = false
    let timer: number | undefined
    const poll = async () => {
      let again = true
      try {
        const result = await api.job(jobId)
        if (!disposed) { setJob(result); setError(""); again = isActive(result) }
      } catch (error) {
        if (!disposed) {
          if (error && typeof error === "object" && "status" in error && error.status === 404) { missing.current(); setJob(null); again = false }
          else setError(message(error))
        }
      }
      if (!disposed && again) timer = window.setTimeout(() => { void poll() }, 2000)
    }
    setJob(knownJobs.find(item => item.id === jobId) ?? null)
    setError("")
    void poll()
    const online = () => { window.clearTimeout(timer); void poll() }
    window.addEventListener("online", online)
    return () => { disposed = true; window.clearTimeout(timer); window.removeEventListener("online", online) }
    // The selected id owns polling; incoming list updates are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])
  useEffect(() => { const latest = knownJobs.find(item => item.id === jobId); if (latest) setJob(latest) }, [knownJobs, jobId])
  return { job: job?.id === jobId ? job : null, error }
}
