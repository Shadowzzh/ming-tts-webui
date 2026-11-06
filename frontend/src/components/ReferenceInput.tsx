import { useEffect, useId, useRef, useState } from "react"
import { AudioLines, LoaderCircle, UploadCloud, X } from "lucide-react"
import { api, message } from "@/lib/api"
import { duration } from "@/lib/format"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Field } from "@/components/fields"
import { AudioPlayer } from "@/components/AudioPlayer"
import type { Options, Params, Upload } from "@/types"
export function ReferenceInput({ params, update, upload, onUpload, options, audioUrl, disabled = false, onBusyChange }: { params: Params; update: (value: Partial<Params>) => void; upload: Upload | null; onUpload: (value: Upload | null) => void; options: Options; audioUrl?: string | null; disabled?: boolean; onBusyChange?: (busy: boolean) => void }) {
  const id = useId()
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [dragging, setDragging] = useState(false)
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false) }, [busy, onBusyChange])
  async function accept(file?: File) {
    if (!file || busy || disabled) return
    setError("")
    if (!/\.(wav|mp3|flac|m4a|ogg)$/i.test(file.name)) { setError("请选择 WAV、MP3、FLAC、M4A 或 OGG 音频。"); return }
    if (file.size > options.limits.max_upload_mb * 1024 * 1024) { setError(`音频大小不能超过 ${options.limits.max_upload_mb} MB。`); return }
    setBusy(true)
    try {
      const result = await api.upload(file)
      if (result.duration_seconds > options.limits.max_reference_seconds) throw new Error(`参考音频不能超过 ${options.limits.max_reference_seconds} 秒。`)
      onUpload(result)
      update({ upload_id: result.upload_id, voice_id: undefined })
    } catch (error) { setError(message(error)) }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = "" }
  }
  const source = upload?.audio_url ?? (params.upload_id ? audioUrl ?? `/api/uploads/${encodeURIComponent(params.upload_id)}` : null)
  return <div className="space-y-3">
    <div className="flex items-center justify-between gap-2"><span className="text-xs font-medium">参考音频 <span className="text-destructive">*</span></span><span className="text-[11px] text-muted-foreground">最长 {options.limits.max_reference_seconds} 秒 · {options.limits.max_upload_mb} MB</span></div>
    <input ref={fileRef} id={id} type="file" accept=".wav,.mp3,.flac,.m4a,.ogg" aria-label="上传参考音频" className="sr-only" disabled={busy || disabled} onChange={event => { void accept(event.target.files?.[0]) }} />
    {source ? <div className="rounded-xl border bg-muted/30 p-3">
      <div className="mb-3 flex items-center gap-2"><AudioLines className="size-4 text-primary" /><span className="min-w-0 flex-1 truncate text-xs font-medium">{upload?.filename ?? "已保存的参考音频"}</span>{upload && <span className="text-xs text-muted-foreground">{duration(upload.duration_seconds)}</span>}<Button type="button" size="icon-sm" variant="ghost" aria-label="移除参考音频" disabled={busy || disabled} onClick={() => { onUpload(null); update({ upload_id: undefined, voice_id: undefined }) }}><X /></Button></div>
      <AudioPlayer key={source} src={source} label="参考音频试听" />
      <Button type="button" size="sm" variant="link" disabled={busy || disabled} className="mt-2 px-0" onClick={() => fileRef.current?.click()}>更换参考音频</Button>
    </div> : <button type="button" disabled={busy || disabled} onClick={() => fileRef.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); void accept(event.dataTransfer.files[0]) }} className={`flex w-full flex-col items-center gap-2 rounded-xl border border-dashed px-4 py-6 text-center transition-colors hover:border-primary/60 hover:bg-primary/5 disabled:opacity-50 ${dragging ? "border-primary bg-primary/5" : "border-border bg-muted/20"}`}>
      {busy ? <LoaderCircle className="size-6 animate-spin text-primary" /> : <UploadCloud className="size-6 text-primary/70" />}
      <span className="text-xs font-medium">{busy ? "正在上传并解析音频…" : "点击上传，或将音频拖到这里"}</span><span className="text-[11px] text-muted-foreground">WAV、MP3、FLAC 等常见音频格式</span>
    </button>}
    {busy && source && <p role="status" className="text-xs text-primary">正在上传新的参考音频…</p>}
    {error && <p role="alert" className="text-xs leading-relaxed text-destructive">{error}</p>}
    <Field label="参考音频对应文字" htmlFor={`${id}-text`} hint="填写准确的参考文字，有助于保留声音的细节。"><Textarea id={`${id}-text`} value={params.prompt_text ?? ""} onChange={event => update({ prompt_text: event.target.value })} placeholder="请输入参考音频中说出的内容…" maxLength={3000} className="min-h-20 resize-y text-sm" disabled={disabled} /></Field>
  </div>
}
