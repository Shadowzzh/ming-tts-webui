import type { Job, Options, Params, Voice } from "@/types"
export const taskLabels: Record<string, string> = { TTS: "声音克隆", MULTILINGUAL: "跨语言配音", BASIC: "基础调音", EMOTION: "情绪表达", DIALECT: "方言配音", FREEFORM: "自由指令", VOICE_DESIGN: "声音设计", IP: "内置音色" }
export const taskDescriptions: Record<string, string> = { TTS: "用一段参考音频，复现独特的声音。", MULTILINGUAL: "保留声音特质，跨越语言边界。", BASIC: "为声音调整语速、音调和音量。", EMOTION: "让每一句话拥有合适的情绪。", DIALECT: "用熟悉的乡音，讲述你的故事。", FREEFORM: "通过自然语言指令，自由控制演绎方式。", VOICE_DESIGN: "描述理想中的声音，让文字成为音色。", IP: "直接使用模型提供的精选声音。" }
export const kindLabels = { clone: "克隆声音", builtin: "内置音色", design: "设计声音" }
export const languageLabels: Record<string, string> = { zh: "中文", en: "英语", ja: "日语", ko: "韩语", de: "德语", fr: "法语", es: "西班牙语" }
export const dialectLabels: Record<string, string> = { cantonese: "粤语", "chuanyu dialect": "川渝方言" }
export const builtinLabels: Record<string, string> = { US_James: "詹姆斯 · 美式男声 / 旁白", US_Stella: "斯黛拉 · 美式女声 / 温暖", US_Emma: "艾玛 · 美式女声 / 助手", UK_George: "乔治 · 英式男声 / 有声书", UK_Charlotte: "夏洛特 · 英式女声 / 沉稳", "灵小柔": "灵小柔 · 清甜女声", "灵楷": "灵楷 · 温润男声", "灵小安": "灵小安 · 少女声线", "灵语汐": "灵语汐 · 明亮女声", "灵朗": "灵朗 · 亲切男声", "灵叙柔": "灵叙柔 · 新闻播音", "灵卓然": "灵卓然 · 纪录片" }
export const tagLabels: Record<string, string> = { "[Breathing]": "呼吸", "[Cough]": "咳嗽", "[Sigh]": "叹气", "[Laughter]": "笑声", "[Uhm]": "嗯", "[Surprise-ah]": "惊叹 · 啊", "[Surprise-oh]": "惊叹 · 哦", "[Dissatisfaction-hnn]": "不满 · 哼", "[Confirmation-en]": "确认 · 嗯", "[Question-ei]": "疑问 · 诶", "[Question-ah]": "疑问 · 啊", "[Question-en]": "疑问 · 嗯", "[Question-oh]": "疑问 · 哦", "[Question-huh]": "疑问 · 哈" }
export const countText = (text: string) => Array.from(text).length
export const isActive = (job: Job) => job.status === "queued" || job.status === "running"
// Older servers only guarantee cancellation while the task is still queued.
export const canCancel = (job: Job) => isActive(job) && job.phase !== "cancelling" && job.phase !== "postprocessing" && (job.can_cancel ?? job.status === "queued")
export const phaseLabel: Record<string, string> = { waiting_gpu: "排队中", starting: "准备中", running: "生成中", cancelling: "正在停止", postprocessing: "降噪中" }
export const statusLabel = (job: Job) => ({ queued: "排队中", running: phaseLabel[job.phase ?? ""] ?? "生成中", completed: "已完成", failed: "生成失败", cancelled: "已取消" }[job.status] ?? job.status)
export function activeMessage(job: Job) {
  if (job.status === "queued" || job.phase === "waiting_gpu") return "任务已加入队列，轮到后会自动开始。"
  if (job.phase === "cancelling") return "正在停止任务，请稍候…"
  if (job.phase === "starting") return "正在准备生成环境，请稍候。"
  if (job.phase === "postprocessing") return "正在完成轻度降噪，处理后会自动显示音频。"
  return "正在生成音频，完成后会自动显示。"
}
// Only phases with real evidence are shown: executor UTC stamps (queue/container)
// and the worker's own model-load / inference counters.
export function timingsText(job: Job) {
  const value = job.timings
  if (!value) return ""
  const parts: Array<[string, number | undefined]> = [
    ["排队", value.queue_seconds], ["准备环境", value.container_seconds],
    ["模型加载", value.model_load_seconds], ["合成", value.inference_seconds],
  ]
  return parts.filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]) && entry[1] > 0.05)
    .map(([label, seconds]) => `${label} ${seconds.toFixed(1)} 秒`).join(" · ")
}
export function duration(seconds: number | null | undefined) { if (seconds == null) return "—"; return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}` }
export function dateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) }
export function snapshot(job: Job): Params { const { voice_id: _voiceId, ...params } = job.parameters; return { ...params, text: job.text } }
export function voiceParams(voice: Voice, text = ""): Params {
  return { ...voice.parameters, task: voice.kind === "builtin" ? "IP" : voice.kind === "design" ? "VOICE_DESIGN" : voice.parameters.task, text, voice_id: voice.id, upload_id: voice.upload_id ?? undefined, prompt_text: voice.prompt_text, ...(voice.kind === "clone" ? { use_spk_emb: voice.parameters.use_spk_emb ?? true } : {}) }
}
export function validateParams(params: Params, options: Options, requireText = true): string | null {
  if (requireText && !params.text?.trim()) return "请先输入要转换为语音的文本。"
  if (countText(params.text ?? "") > options.limits.max_text_length) return `文本不能超过 ${options.limits.max_text_length} 字。`
  const task = options.tasks.find(item => item.value === params.task)
  if (!task) return "请选择有效的生成模式。"
  if (task.needs_prompt && !params.upload_id) return "当前模式需要参考音频，请上传音频或选择克隆声音。"
  if (!task.language_optional && !params.language_id) return "跨语言配音需要选择目标语言。"
  if (params.use_spk_emb && params.use_zero_spk_emb) return "参考说话人特征与零说话人特征不能同时启用。"
  const labels: Record<string, string> = { instruction: "演绎指令", style: "声音描述", ip: "内置音色", dialect: "方言", emotion: "情绪", speed: "语速", pitch: "音调", volume: "音量" }
  for (const field of task.controls) if (!(params[field as keyof Params] ?? "").toString().trim()) return `请设置${labels[field] ?? field}。`
  const bounds = { cfg: [0, 10, false], sigma: [0, 1, false], temperature: [0, 2, false], max_length: [1, 60, true], max_decode_steps: [50, 2000, true], seed: [0, 2147483647, true] } as const
  for (const key of Object.keys(bounds) as (keyof typeof bounds)[]) {
    const value = params[key]
    if (value === undefined) continue
    const [min, max, integer] = bounds[key]
    if (!Number.isFinite(value) || value < min || value > max || integer && !Number.isInteger(value)) return `${key} 需要在 ${min}–${max} 之间${integer ? "，且为整数" : ""}。`
  }
  return null
}
