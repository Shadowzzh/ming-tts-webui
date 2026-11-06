import { useId, useState } from "react"
import { LoaderCircle, Mic2 } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Choice, Field } from "@/components/fields"
import { AdvancedParameters, ParameterFields } from "@/components/ParameterFields"
import { ReferenceInput } from "@/components/ReferenceInput"
import { kindLabels, validateParams } from "@/lib/format"
import type { Draft, Options, Params, Upload, Voice, VoiceInput, VoiceKind } from "@/types"
export function VoiceEditor({ voice, seed, options, onClose, onSave }: { voice?: Voice; seed?: Draft; options: Options; onClose: () => void; onSave: (body: VoiceInput, id?: string) => Promise<string | null> }) {
  const id = useId()
  const starting = voice?.parameters ?? seed?.params ?? { task: "TTS" }
  const [kind, setKind] = useState<VoiceKind>(voice?.kind ?? (starting.task === "IP" ? "builtin" : starting.task === "VOICE_DESIGN" ? "design" : "clone"))
  const [name, setName] = useState(voice?.name ?? "")
  const [description, setDescription] = useState(voice?.description ?? "")
  const [tags, setTags] = useState(voice?.tags.join("，") ?? "")
  const [favorite, setFavorite] = useState(voice?.favorite ?? false)
  const [params, setParams] = useState<Params>({ ...starting, task: kind === "builtin" ? "IP" : kind === "design" ? "VOICE_DESIGN" : !options.tasks.find(t => t.value === starting.task)?.needs_prompt ? "TTS" : starting.task, upload_id: voice?.upload_id ?? starting.upload_id, prompt_text: voice?.prompt_text ?? starting.prompt_text, use_spk_emb: starting.use_spk_emb ?? true })
  const [upload, setUpload] = useState<Upload | null>(seed?.upload ?? null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const update = (value: Partial<Params>) => setParams(old => ({ ...old, ...value }))
  function changeKind(value: string) { const next = value as VoiceKind; setKind(next); update({ task: next === "clone" ? "TTS" : next === "builtin" ? "IP" : "VOICE_DESIGN" }); setError("") }
  async function submit() {
    if (busy || uploading) return
    setError("")
    if (!name.trim()) { setError("请为声音填写一个名称。"); return }
    const tagValues = [...new Set(tags.split(/[,，\n]/).map(item => item.trim()).filter(Boolean))]
    if (tagValues.length > 12 || tagValues.some(tag => Array.from(tag).length > 30)) { setError("最多可添加 12 个标签，每个标签不超过 30 字。"); return }
    const { text: _text, voice_id: _voiceId, ...parameters } = params
    const validation = validateParams({ ...parameters, task: kind === "builtin" ? "IP" : kind === "design" ? "VOICE_DESIGN" : parameters.task }, options, false)
    if (validation) { setError(validation); return }
    setBusy(true)
    const failure = await onSave({ name: name.trim(), kind, description: description.trim(), tags: tagValues, favorite, upload_id: kind === "clone" ? params.upload_id ?? null : null, prompt_text: kind === "clone" ? params.prompt_text ?? "" : "", parameters }, voice?.id)
    setBusy(false)
    if (!failure) onClose()
    else setError(failure)
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent className="max-h-[90dvh] gap-0 overflow-y-auto p-0 sm:max-w-xl" onInteractOutside={event => { if (busy) event.preventDefault() }}>
    <DialogHeader className="border-b p-6"><DialogTitle className="flex items-center gap-2"><Mic2 className="size-5 text-primary" />{voice ? "编辑声音" : "创建声音预设"}</DialogTitle><DialogDescription>保存独特的音色与参数，下次创作时一键使用。</DialogDescription></DialogHeader>
    <form onSubmit={event => { event.preventDefault(); void submit() }} className="space-y-5 p-6">
      <div className="grid gap-4 sm:grid-cols-2"><Field label="声音名称 *" htmlFor={`${id}-name`}><Input id={`${id}-name`} value={name} maxLength={80} onChange={event => setName(event.target.value)} placeholder="给这个声音起个名字" autoFocus required disabled={busy} /></Field><Choice label="声音类型" value={kind} onChange={changeKind} items={Object.entries(kindLabels).map(([value, label]) => ({ value, label }))} disabled={busy} /></div>
      <Field label="备注" htmlFor={`${id}-description`}><Textarea id={`${id}-description`} value={description} onChange={event => setDescription(event.target.value)} placeholder="记录声音特点、适用场景…" maxLength={1000} disabled={busy} /></Field>
      <Field label="标签" htmlFor={`${id}-tags`} hint="使用中文或英文逗号分隔。"><Input id={`${id}-tags`} value={tags} onChange={event => setTags(event.target.value)} placeholder="旁白，温暖，有声书" disabled={busy} /></Field>
      <div className="flex items-center justify-between"><Label htmlFor={`${id}-favorite`} className="text-xs">收藏这个声音</Label><Switch id={`${id}-favorite`} checked={favorite} onCheckedChange={setFavorite} disabled={busy} /></div>
      <div className="space-y-5 rounded-xl border bg-muted/20 p-4">
        <ParameterFields params={params} update={update} options={options} hideTask={kind !== "clone"} taskChoices={options.tasks.filter(task => task.needs_prompt)} />
        {kind === "clone" && <ReferenceInput params={params} update={update} upload={upload} onUpload={setUpload} options={options} audioUrl={voice?.audio_url} disabled={busy} onBusyChange={setUploading} />}
        <AdvancedParameters params={params} update={update} options={options} />
      </div>
      {error && <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive">{error}</p>}
      <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button type="submit" disabled={busy || uploading}>{busy && <LoaderCircle className="size-4 animate-spin" />}{busy ? "正在保存…" : "保存声音"}</Button></DialogFooter>
    </form>
  </DialogContent></Dialog>
}
