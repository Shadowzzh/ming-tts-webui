import type { Job, JobList, Options, Params, ServiceStatus, Upload, Voice, VoiceInput } from "@/types"

export class ApiError extends Error {
  status: number
  constructor(message: string, status = 0) { super(message); this.name = "ApiError"; this.status = status }
}
function errorDetail(data: unknown): string {
  if (!data || typeof data !== "object") return ""
  const body = data as Record<string, unknown>
  const detail = body.detail ?? body.error ?? body.message
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) return detail.map(item => typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)).join("；")
  return ""
}
async function request<T>(path: string, init: RequestInit = {}, timeout = 20000): Promise<T> {
  try {
    const response = await fetch(path, { ...init, signal: init.signal ?? AbortSignal.timeout(timeout), headers: init.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init.headers } })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new ApiError(errorDetail(body) || `请求失败（HTTP ${response.status}），请稍后重试。`, response.status)
    if (body === null) throw new ApiError("服务返回了无法识别的数据，请检查服务状态。", response.status)
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === "AbortError") throw error
    throw new ApiError(error instanceof Error && /timeout/i.test(error.name) ? "请求超时，请检查服务连接。提交任务后请先查看生成记录，避免重复提交。" : "无法连接到服务。请检查网络与后端，连接恢复后会自动更新。")
  }
}
const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) })
const id = encodeURIComponent
export const api = {
  options: () => request<Options>("/api/options"),
  status: () => request<ServiceStatus>("/api/status"),
  voices: () => request<{ items: Voice[] }>("/api/voices"),
  createVoice: (body: VoiceInput) => request<Voice>("/api/voices", json("POST", body)),
  updateVoice: (voiceId: string, body: Partial<VoiceInput>) => request<Voice>(`/api/voices/${id(voiceId)}`, json("PATCH", body)),
  deleteVoice: (voiceId: string) => request<{ ok: boolean }>(`/api/voices/${id(voiceId)}`, json("DELETE")),
  upload: (file: File) => { const body = new FormData(); body.append("file", file); return request<Upload>("/api/upload_prompt", { method: "POST", body }, 120000) },
  jobs: (q = "", favorite = false) => request<JobList>(`/api/jobs?${new URLSearchParams({ q, limit: "100", ...(favorite ? { favorite: "1" } : {}) })}`),
  job: (jobId: string) => request<Job>(`/api/jobs/${id(jobId)}`),
  createJob: (body: Params) => request<Job>("/api/jobs", json("POST", body)),
  updateJob: (jobId: string, body: { favorite?: boolean; title?: string }) => request<Job>(`/api/jobs/${id(jobId)}`, json("PATCH", body)),
  cancelJob: (jobId: string) => request<Job>(`/api/jobs/${id(jobId)}/cancel`, json("POST")),
  deleteJob: (jobId: string) => request<{ ok: boolean }>(`/api/jobs/${id(jobId)}`, json("DELETE")),
}
export function message(error: unknown) { return error instanceof Error ? error.message : "操作失败，请稍后重试。" }
