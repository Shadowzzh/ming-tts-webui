import { useEffect, useRef, useState } from "react"
import { ArrowRight, AudioLines, ChevronDown, FileText, History, Keyboard, LoaderCircle, Mic2, Plus, Save, Sparkles, WandSparkles, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Textarea } from "@/components/ui/textarea"
import { Choice } from "@/components/fields"
import { AdvancedParameters, ParameterFields } from "@/components/ParameterFields"
import { ReferenceInput } from "@/components/ReferenceInput"
import { JobCard, type JobCallbacks } from "@/components/JobCard"
import { EmptyState } from "@/components/EmptyState"
import { countText, taskDescriptions, tagLabels, validateParams } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Draft, Job, Options, Params, ServiceStatus, Upload, Voice } from "@/types"
interface Props extends JobCallbacks {
  draft: Draft; update: (value: Partial<Params>) => void; onUpload: (upload: Upload | null) => void; options: Options;
  voices: Voice[]; status: ServiceStatus | null; jobs: Job[]; activeJob: Job | null; activeError: string; generating: boolean;
  busy: boolean; onGenerate: () => void; onSelectVoice: (voice: Voice | null) => void; onSaveVoice: () => void;
  onHistory: () => void; onVoices: () => void;
}
export function StudioView({ draft, update, onUpload, options, voices, status, jobs, activeJob, activeError, generating, busy, onGenerate, onSelectVoice, onSaveVoice, onHistory, onVoices, ...jobCallbacks }: Props) {
  const editor = useRef<HTMLTextAreaElement>(null)
  const file = useRef<HTMLInputElement>(null)
  const [inputError, setInputError] = useState("")
  const [importing, setImporting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const { params } = draft
  const text = params.text ?? ""
  const count = countText(text)
  const limit = options.limits.max_text_length
  const task = options.tasks.find(item => item.value === params.task)
  const selectedVoice = voices.find(voice => voice.id === params.voice_id)
  const validation = validateParams(params, options)
  const queueFull = !!status && status.queue.queued >= options.limits.max_queue_size
  const disabled = generating || importing || uploading || !!validation || queueFull
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !event.repeat && !(event.target instanceof Element && event.target.closest('[role="dialog"],[role="alertdialog"]'))) {
        event.preventDefault()
        if (!disabled) onGenerate()
      }
    }
    window.addEventListener("keydown", shortcut)
    return () => window.removeEventListener("keydown", shortcut)
  }, [disabled, onGenerate])
  function insertTag(tag: string) {
    const start = editor.current?.selectionStart ?? text.length
    const end = editor.current?.selectionEnd ?? text.length
    const next = text.slice(0, start) + tag + text.slice(end)
    if (countText(next) > limit) { setInputError("插入标签后会超出字数上限，请先缩短文本。"); return }
    update({ text: next })
    requestAnimationFrame(() => { editor.current?.focus(); editor.current?.setSelectionRange(start + tag.length, start + tag.length) })
  }
  async function importText(selected?: File) {
    if (!selected) return
    setInputError(""); setImporting(true)
    try {
      if (!/\.txt$/i.test(selected.name)) throw new Error("请选择 UTF-8 编码的 .txt 文件。")
      if (selected.size > limit * 4 + 3) throw new Error(`文本文件过大，请控制在 ${limit} 字以内。`)
      let value: string
      try { value = new TextDecoder("utf-8", { fatal: true }).decode(await selected.arrayBuffer()) }
      catch { throw new Error("文件不是有效的 UTF-8 文本，请转换编码后重新导入。") }
      if (countText(value) > limit) throw new Error(`导入文本共 ${countText(value)} 字，超过 ${limit} 字上限。当前草稿已保留。`)
      if (!value.trim()) throw new Error("文本文件为空，请选择包含文字的文件。")
      update({ text: value })
    } catch (error) { setInputError(error instanceof Error ? error.message : "读取文件失败。") }
    finally { setImporting(false); if (file.current) file.current.value = "" }
  }
  return <div className="space-y-7">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow"><span className="mr-2 inline-block size-1.5 rounded-full bg-primary" />让文字拥有声音</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">好声音，从这里开始<span className="text-primary">。</span></h1><p className="mt-3 text-sm text-muted-foreground">写下你的灵感，赋予它独一无二的声线。</p></div><Button type="button" variant="outline" className="h-9 bg-card text-xs" onClick={onVoices}><Mic2 className="size-3.5" />探索声音库<ArrowRight className="size-3.5" /></Button></div>
    <div className="studio-grid">
      <div className="min-w-0 space-y-6">
        <Card className="gap-0 overflow-hidden rounded-2xl border py-0 shadow-xs">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4"><div className="flex items-center gap-2.5"><div className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary"><FileText className="size-3.5" /></div><h2 className="text-sm font-semibold">创作文本</h2></div><div className="flex items-center gap-1"><input ref={file} type="file" accept=".txt,text/plain" aria-label="导入 UTF-8 文本文件" className="sr-only" onChange={event => { void importText(event.target.files?.[0]) }} /><Button type="button" variant="ghost" size="sm" disabled={importing} onClick={() => file.current?.click()} className="text-xs text-muted-foreground">{importing ? <LoaderCircle className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}导入文本</Button><Button type="button" variant="ghost" size="icon-sm" aria-label="清空创作文本" onClick={() => update({ text: "" })} disabled={!text}><X className="size-3.5 text-muted-foreground" /></Button></div></div>
          <CardContent className="p-0"><label className="sr-only" htmlFor="creation-text">要生成语音的文本</label><Textarea ref={editor} id="creation-text" value={text} onChange={event => { update({ text: event.target.value }); setInputError("") }} placeholder={"在这里，写下你想让世界听见的话。\n\n一段故事、一句问候，或是刚刚闪过的灵感…"} className="editor-textarea min-h-72 resize-y rounded-none border-0 bg-transparent px-6 py-6 text-base leading-8 shadow-none focus-visible:ring-0 md:min-h-80" aria-describedby="text-count" aria-invalid={count > limit} />
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-dashed px-5 py-3"><DropdownMenu><DropdownMenuTrigger asChild><Button type="button" size="sm" variant="ghost" className="h-7 px-0 text-xs text-muted-foreground"><Sparkles className="size-3.5 text-primary" />插入声音标签<ChevronDown className="size-3" /></Button></DropdownMenuTrigger><DropdownMenuContent align="start" className="max-h-80 overflow-y-auto"><DropdownMenuLabel className="text-xs text-muted-foreground">在光标位置添加非语言表达</DropdownMenuLabel><DropdownMenuSeparator />{options.paralinguistic_tags.map(tag => <DropdownMenuItem key={tag} onSelect={() => insertTag(tag)}><span className="flex-1">{tagLabels[tag] ?? tag}</span><span className="ml-5 font-mono text-[10px] text-muted-foreground">{tag}</span></DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu><span id="text-count" className={cn("font-mono text-[11px] tabular-nums text-muted-foreground", count > limit && "text-destructive")}><span className={cn(count > 0 && count <= limit && "text-foreground")}>{count.toLocaleString("zh-CN")}</span> / {limit.toLocaleString("zh-CN")} 字</span></div>
          </CardContent>
        </Card>
        {inputError && <p role="alert" className="rounded-xl border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive">{inputError}</p>}
        <section aria-labelledby="result-heading"><div className="mb-3 flex items-center justify-between"><h2 id="result-heading" className="flex items-center gap-2 text-sm font-semibold"><AudioLines className="size-4 text-primary" />声音预览</h2><Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">生成结果</Badge></div>{activeError && <p role="alert" className="mb-3 rounded-lg bg-destructive/5 p-3 text-xs text-destructive">{activeError}</p>}{activeJob ? <JobCard job={activeJob} compact busy={busy} {...jobCallbacks} /> : <div className="preview-empty rounded-2xl border bg-card"><EmptyState title="让灵感被听见" description="完成右侧的声音设置，点击「生成语音」。音频准备好后，会出现在这里。" icon={<AudioLines className="size-7" />} /></div>}</section>
        {jobs.length > 0 && <section><div className="mb-3 flex items-center justify-between"><h2 className="flex items-center gap-2 text-sm font-semibold"><History className="size-4 text-muted-foreground" />最近生成</h2><Button type="button" variant="link" size="sm" className="h-auto p-0 text-xs" onClick={onHistory}>全部记录<ArrowRight className="size-3" /></Button></div><div className="space-y-3">{jobs.filter(job => job.id !== activeJob?.id).slice(0, 2).map(job => <JobCard key={job.id} job={job} compact busy={busy} {...jobCallbacks} />)}</div></section>}
        <p className="flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground"><Save className="size-3" />草稿自动保存在此浏览器 · 音频由本地模型生成</p>
      </div>
      <aside className="min-w-0 space-y-4">
        <Card className="gap-0 rounded-2xl py-0 shadow-xs"><div className="flex items-center justify-between border-b px-5 py-4"><h2 className="flex items-center gap-2 text-sm font-semibold"><WandSparkles className="size-4 text-primary" />声音设置</h2><Button type="button" variant="ghost" size="sm" className="h-7 px-1 text-[11px] text-primary" onClick={onSaveVoice}><Plus className="size-3" />保存预设</Button></div><CardContent className="space-y-5 p-5">
          <Choice label="声音预设" value={selectedVoice?.id ?? "__default"} onChange={value => onSelectVoice(voices.find(voice => voice.id === value) ?? null)} items={[{ value: "__default", label: "模型默认 / 自定义声音" }, ...voices.map(voice => ({ value: voice.id, label: `${voice.favorite ? "♥ " : ""}${voice.name}` }))]} />
          {selectedVoice && <p className="-mt-2 text-[11px] leading-5 text-muted-foreground">{selectedVoice.description || "已加载该声音的参数与参考音频。"}</p>}
          <div className="border-t pt-5"><ParameterFields params={params} update={update} options={options} /><p className="mt-2 text-[11px] leading-5 text-muted-foreground">{taskDescriptions[params.task]}</p></div>
          {task?.needs_prompt && <div className="border-t pt-5"><ReferenceInput params={params} update={update} upload={draft.upload} onUpload={onUpload} options={options} audioUrl={selectedVoice?.audio_url} onBusyChange={setUploading} /></div>}
          <AdvancedParameters params={params} update={update} options={options} />
        </CardContent></Card>
        <div className="generate-box rounded-2xl border border-primary/15 bg-card p-4 shadow-xs">
          {queueFull && <p role="alert" className="mb-3 text-xs text-destructive">队列已满，请等待已有任务完成后再试。</p>}
          <Button type="button" onClick={onGenerate} disabled={disabled} className="h-12 w-full gap-2 rounded-xl text-sm shadow-sm">{generating ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}{generating ? "正在提交任务…" : uploading ? "参考音上传中…" : "生成语音"}<ArrowRight className="ml-1 size-4 opacity-70" /></Button>
          {validation && text.trim() && <p className="mt-2.5 text-[11px] leading-5 text-muted-foreground">{validation}</p>}
          <div className="mt-3 flex items-center justify-between gap-2 text-[10px] text-muted-foreground"><span className="flex items-center gap-1"><Keyboard className="size-3" />⌘ / Ctrl + Enter</span><span>WAV 无损音频</span></div>
        </div>
      </aside>
    </div>
  </div>
}
