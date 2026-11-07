export type TaskValue = "TTS" | "MULTILINGUAL" | "BASIC" | "EMOTION" | "DIALECT" | "FREEFORM" | "VOICE_DESIGN" | "IP"
export interface Option { value: string; label: string }
export interface TaskOption extends Option { value: TaskValue; needs_prompt: boolean; controls: string[]; language_optional: boolean }
export interface Params {
  text?: string; task: TaskValue; language_id?: string; voice_id?: string; upload_id?: string;
  prompt_text?: string; use_spk_emb?: boolean; use_zero_spk_emb?: boolean; instruction?: string;
  ip?: string; emotion?: string; speed?: string; pitch?: string; volume?: string;
  dialect?: string; style?: string; cfg?: number; sigma?: number; temperature?: number;
  max_length?: number; max_decode_steps?: number; seed?: number; no_trim_silence?: boolean; raw_text?: boolean;
}
export interface Options {
  tasks: TaskOption[]; languages: Option[]; emotions: Option[]; speed: Option[]; pitch: Option[]; volume: Option[];
  dialects: string[]; builtin_voices: Option[]; defaults: Required<Pick<Params, "cfg" | "sigma" | "temperature" | "max_length" | "max_decode_steps" | "seed">>;
  paralinguistic_tags: string[];
  limits: { max_text_length: number; max_upload_mb: number; max_reference_seconds: number; max_queue_size: number };
}
export type Backend = "local" | "ray"
export type JobPhase = "waiting_gpu" | "starting" | "running" | "cancelling"
export interface RayStatus { url: string; profile: string; reachable: boolean; dispatch_dir?: string; version?: string | null; error?: string | null }
export interface JobTimings {
  queue_seconds?: number; container_seconds?: number; model_load_seconds?: number;
  inference_seconds?: number; total_seconds?: number;
}
export interface ExecutionCapabilities {
  cancel_running: boolean; reports_progress: boolean; resume_running: boolean;
}
export interface ServiceStatus {
  loaded: boolean; device: string; sample_rate: number | null;
  capabilities?: ExecutionCapabilities;
  backend?: Backend; model_loading?: "per-job" | "loaded"; ray?: RayStatus;
  gpu: { cuda_available?: boolean | null; name?: string; memory_allocated_mb?: number; memory_reserved_mb?: number; remote?: boolean };
  queue: { queued: number; running: number }; storage: { voices: number; jobs: number }; version: string;
}
export interface Upload { upload_id: string; filename: string; audio_url: string; duration_seconds: number; sample_rate: number }
export type VoiceKind = "clone" | "builtin" | "design"
export interface Voice {
  id: string; name: string; kind: VoiceKind; description: string; tags: string[]; favorite: boolean;
  upload_id: string | null; prompt_text: string; parameters: Params; created_at: string; updated_at: string; audio_url: string | null;
}
export type VoiceInput = Pick<Voice, "name" | "kind" | "description" | "tags" | "favorite" | "upload_id" | "prompt_text" | "parameters">
export interface Job {
  id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled";
  text: string; title: string; voice_id: string | null; voice_name: string; parameters: Params;
  created_at: string; started_at: string | null; finished_at: string | null; error: string | null;
  filename: string | null; audio_url: string | null; download_url: string | null; duration_seconds: number | null;
  sample_rate: number | null; elapsed_seconds: number | null; favorite: boolean;
  can_cancel?: boolean;
  backend?: Backend; phase?: JobPhase | null; timings?: JobTimings | null;
  phase_detail?: { ray_status?: string; executor_state?: string | null } | null;
}
export interface JobList { items: Job[]; total: number }
export type View = "studio" | "voices" | "history"
export interface Draft { params: Params; upload: Upload | null }
