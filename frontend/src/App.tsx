import { useEffect, useRef, useState } from "react"
import { AlertCircle, ArrowUpRight, AudioLines, BookAudio, CheckCircle2, ChevronDown, CircleHelp, Cpu, History, LayoutDashboard, LoaderCircle, Monitor, Moon, Radio, RefreshCw, Sparkles, Sun, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ConfirmDialog, type Confirmation } from "@/components/ConfirmDialog"
import { VoiceEditor } from "@/components/VoiceEditor"
import { ParameterGuide } from "@/components/ParameterHelp"
import { StudioView } from "@/views/StudioView"
import { VoicesView } from "@/views/VoicesView"
import { HistoryView } from "@/views/HistoryView"
import { useActiveJob, useStudioData } from "@/hooks/useStudioData"
import { useStoredState } from "@/hooks/useStoredState"
import { api, message } from "@/lib/api"
import { isActive, snapshot, validateParams, voiceParams } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Draft, Job, Params, View, Voice, VoiceInput } from "@/types"

const blankDraft: Draft = { params: { task: "TTS", text: "", use_spk_emb: true }, upload: null }
function validDraft(value: unknown): value is Draft {
  if (!value || typeof value !== "object" || !("params" in value) || !("upload" in value)) return false
  const params = value.params
  return !!params && typeof params === "object" && "task" in params && ["TTS", "MULTILINGUAL", "BASIC", "EMOTION", "DIALECT", "FREEFORM", "VOICE_DESIGN", "IP"].includes(String(params.task)) && (!("text" in params) || typeof params.text === "string")
}
const nav = [{ view: "studio", label: "创作工作台", icon: LayoutDashboard }, { view: "voices", label: "声音库", icon: BookAudio }, { view: "history", label: "生成记录", icon: History }] as const
const resourceLabels = { options: "生成选项", status: "服务状态", voices: "声音库", jobs: "生成记录" }
type Notice = { kind: "success" | "error"; text: string } | null

export default function App() {
  const [view, setView] = useState<View>("studio")
  const data = useStudioData()
  const [draft, setDraft, storageWarning] = useStoredState<Draft>("ming-studio.draft.v2", blankDraft, validDraft)
  const [activeId, setActiveId, activeStorageWarning] = useStoredState<string | null>("ming-studio.active.v2", null, (v): v is string | null => v === null || typeof v === "string")
  const { job: activeJob, error: activeError } = useActiveJob(activeId, data.jobs, () => setActiveId(null))
  const [theme, setTheme, themeWarning] = useStoredState<"light" | "dark" | "system">("ming-studio.theme", "system", (v): v is "light" | "dark" | "system" => v === "light" || v === "dark" || v === "system")
  const [notice, setNotice] = useState<Notice>(null)
  const [busy, setBusy] = useState(false)
  const actionLock = useRef(false)
  const [generating, setGenerating] = useState(false)
  const submissionLock = useRef(false)
  const [editor, setEditor] = useState<{ voice?: Voice; seed?: Draft } | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [rename, setRename] = useState<Job | null>(null)
  const [renameTitle, setRenameTitle] = useState("")
  const [help, setHelp] = useState(false)

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => { document.documentElement.classList.toggle("dark", theme === "dark" || theme === "system" && media.matches); document.documentElement.style.colorScheme = theme === "system" ? (media.matches ? "dark" : "light") : theme }
    apply(); media.addEventListener("change", apply)
    return () => media.removeEventListener("change", apply)
  }, [theme])
  useEffect(() => { if (notice?.kind !== "success") return; const timer = window.setTimeout(() => setNotice(null), 6500); return () => window.clearTimeout(timer) }, [notice])
  function navigate(next: View) { setView(next); window.scrollTo({ top: 0, behavior: "smooth" }) }
  function update(value: Partial<Params>) { setDraft(old => ({ ...old, params: { ...old.params, ...value } })) }
  async function run<T,>(action: () => Promise<T>, success?: string): Promise<{ ok: true; value: T } | { ok: false; error: string }> {
    if (actionLock.current) return { ok: false, error: "另一个操作正在进行，请稍候重试。" }
    actionLock.current = true; setBusy(true)
    try {
      const value = await action()
      if (success) setNotice({ kind: "success", text: success })
      await data.refresh()
      return { ok: true, value }
    } catch (error) { const text = message(error); setNotice({ kind: "error", text }); return { ok: false, error: text } }
    finally { actionLock.current = false; setBusy(false) }
  }
  async function generate(params = draft.params) {
    if (submissionLock.current || !data.options) return
    const validation = validateParams(params, data.options)
    if (validation) { setNotice({ kind: "error", text: validation }); return }
    submissionLock.current = true; setGenerating(true)
    try {
      const job = await api.createJob({ ...data.options.defaults, ...params, use_spk_emb: params.use_spk_emb ?? true })
      setActiveId(job.id)
      if (window.matchMedia("(max-width: 767px)").matches) document.getElementById("result-heading")?.scrollIntoView({ behavior: "smooth", block: "start" })
      setNotice({ kind: "success", text: "任务已提交。生成完成后，音频会自动出现在预览中。" })
      await data.refresh()
    } catch (error) { setNotice({ kind: "error", text: message(error) }) }
    finally { submissionLock.current = false; setGenerating(false) }
  }
  function useVoice(voice: Voice | null) {
    setDraft(old => voice ? { params: voiceParams(voice, old.params.text), upload: null } : { params: { task: "TTS", text: old.params.text ?? "", use_spk_emb: true }, upload: null })
    if (voice) { navigate("studio"); setNotice({ kind: "success", text: `已载入「${voice.name}」的声音参数。` }) }
  }
  function restore(job: Job) {
    setDraft({ params: snapshot(job), upload: null }); navigate("studio")
    setNotice({ kind: "success", text: "已恢复生成时的文本与参数快照，可继续编辑。" })
  }
  const jobCallbacks = {
    onRestore: restore,
    onRetry: (job: Job) => { navigate("studio"); void generate(snapshot(job)) },
    onFavorite: (job: Job) => { void run(() => api.updateJob(job.id, { favorite: !job.favorite }), job.favorite ? "已取消收藏。" : "已收藏音频。") },
    onCancel: (job: Job) => { void run(async () => { const result = await api.cancelJob(job.id); setActiveId(result.id); return result }, "已取消排队任务。") },
    onDelete: (job: Job) => { if (isActive(job)) return; setConfirmation({ title: "删除这条生成记录？", description: "这会同时删除生成的音频文件，删除后无法恢复。声音预设会继续保留。", confirm: async () => { const result = await run(() => api.deleteJob(job.id), "生成记录已删除。"); if (result.ok && activeId === job.id) setActiveId(null); return result.ok } }) },
    onRename: (job: Job) => { setRename(job); setRenameTitle(job.title) },
  }
  async function saveVoice(body: VoiceInput, voiceId?: string) {
    const result = await run(() => voiceId ? api.updateVoice(voiceId, body) : api.createVoice(body), voiceId ? "声音预设已更新。" : "新的声音已加入声音库。")
    if (result.ok && voiceId && draft.params.voice_id === voiceId) setDraft(old => ({ params: voiceParams(result.value, old.params.text), upload: null }))
    return result.ok ? null : result.error
  }
  function createVoice(seed?: Draft) {
    if (!data.options) { setNotice({ kind: "error", text: "生成选项尚未载入，请先恢复服务连接。" }); return }
    setEditor({ seed })
  }
  const disconnected = !!data.errors.status
  const connected = data.status && !disconnected
  const warnings = [...new Set([storageWarning, activeStorageWarning, themeWarning].filter(Boolean))]
  const queueCount = (data.status?.queue.queued ?? 0) + (data.status?.queue.running ?? 0)

  return <div className="app-shell">
    <aside className="desktop-sidebar">
      <a href="#studio" onClick={event => { event.preventDefault(); navigate("studio") }} className="brand"><span className="brand-mark"><AudioLines className="size-6" /></span><span><span className="block text-xl font-semibold tracking-tight">Ming<span className="ml-1 text-primary">.</span></span><span className="mt-0.5 block text-[10px] tracking-[0.14em] text-muted-foreground">声音工作室</span></span></a>
      <div className="px-3 pt-9"><p className="mb-3 px-3 text-[10px] tracking-widest text-muted-foreground">工作空间</p><nav className="space-y-1" aria-label="主导航">{nav.map(item => <button key={item.view} type="button" onClick={() => navigate(item.view)} aria-current={view === item.view ? "page" : undefined} className={cn("nav-item", view === item.view && "active")}><item.icon className="size-4" /><span className="flex-1 text-left">{item.label}</span>{item.view === "history" && queueCount > 0 && <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">{queueCount}</span>}</button>)}</nav></div>
      <div className="mt-auto p-4"><div className="rounded-xl border bg-background/70 p-3.5"><div className="flex items-center gap-2"><span className={cn("size-1.5 rounded-full", connected ? "bg-emerald-500" : disconnected ? "bg-destructive" : "bg-amber-500")} /><span className="text-xs font-medium">{disconnected ? "服务连接中断" : connected ? data.status?.loaded ? "模型已就绪" : "服务已连接" : "正在连接服务"}</span></div><p className="mt-2 truncate text-[10px] text-muted-foreground">{data.status?.gpu.name || data.status?.device || "等待本地服务响应"}</p>{connected && <p className="mt-1.5 text-[10px] text-muted-foreground">{data.status?.queue.running ?? 0} 个生成中 · {data.status?.queue.queued ?? 0} 个排队中</p>}</div><button type="button" onClick={() => setHelp(true)} className="mt-4 flex w-full items-center gap-2 px-1 text-xs text-muted-foreground hover:text-foreground"><CircleHelp className="size-3.5" />使用指南<ArrowUpRight className="ml-auto size-3" /></button><div className="mt-5 flex items-center justify-between border-t px-1 pt-4 text-[10px] text-muted-foreground"><span>本地运行 · 自由创作</span><span>v{data.status?.version ?? "2.0.0"}</span></div></div>
    </aside>
    <div className="workspace">
      <header className="workspace-header"><div className="flex min-w-0 items-center gap-2"><span className="flex size-7 items-center justify-center rounded-lg bg-primary text-white lg:hidden"><AudioLines className="size-4" /></span><span className="hidden text-xs text-muted-foreground sm:inline">我的工作空间</span><span className="hidden text-xs text-border sm:inline">/</span><span className="truncate text-xs font-medium">{nav.find(item => item.view === view)?.label}</span></div><div className="flex items-center gap-2">
        <DropdownMenu><DropdownMenuTrigger asChild><Button type="button" variant="ghost" size="sm" className="gap-1.5 text-[11px] text-muted-foreground"><span className={cn("size-1.5 rounded-full", connected ? "bg-emerald-500" : disconnected ? "bg-destructive" : "bg-amber-500")} /><span className="hidden sm:inline">{connected ? "本地服务" : disconnected ? "连接中断" : "连接中"}</span>{queueCount > 0 && <span>{queueCount} 个任务</span>}<ChevronDown className="size-3" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end" className="w-64"><DropdownMenuLabel>运行状态</DropdownMenuLabel><div className="space-y-2 px-2 py-2 text-xs text-muted-foreground"><p>模型：{data.status ? data.status.loaded ? "已加载" : "首次生成时加载" : "尚未连接"}</p><p className="break-words">设备：{data.status?.gpu.name || data.status?.device || "—"}</p><p>采样率：{data.status?.sample_rate ? `${data.status.sample_rate.toLocaleString()} Hz` : "—"}</p>{data.status?.gpu.memory_allocated_mb != null && <p>显存占用：{data.status.gpu.memory_allocated_mb.toFixed(0)} MB</p>}<p>队列：{data.status?.queue.running ?? "—"} 运行 / {data.status?.queue.queued ?? "—"} 等待</p></div><DropdownMenuSeparator /><DropdownMenuItem onSelect={() => { void data.refresh() }}><RefreshCw />重新连接与刷新</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
        <span className="mx-1 h-4 w-px bg-border" /><DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" type="button" aria-label="切换外观主题">{theme === "dark" ? <Moon className="size-4" /> : theme === "light" ? <Sun className="size-4" /> : <Monitor className="size-4" />}</Button></DropdownMenuTrigger><DropdownMenuContent align="end">{([{ value: "light", label: "浅色模式", icon: Sun }, { value: "dark", label: "深色模式", icon: Moon }, { value: "system", label: "跟随系统", icon: Monitor }] as const).map(item => <DropdownMenuItem key={item.value} onSelect={() => setTheme(item.value)}><item.icon />{item.label}{theme === item.value && <CheckCircle2 className="ml-auto size-3" />}</DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu>
      </div></header>
      <nav className="mobile-nav" aria-label="移动导航">{nav.map(item => <button key={item.view} type="button" onClick={() => navigate(item.view)} aria-current={view === item.view ? "page" : undefined} className={cn("flex flex-1 items-center justify-center gap-2 rounded-lg px-2 py-2.5 text-xs", view === item.view ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground")}><item.icon className="size-4" />{item.label}</button>)}</nav>
      <main className="workspace-main">
        {Object.values(data.errors).some(Boolean) && <div role="alert" className="mb-5 flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-4"><AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" /><div className="min-w-0 flex-1 space-y-1">{Object.entries(data.errors).filter(([, value]) => value).map(([key, value]) => <p key={key} className="break-words text-xs leading-5 text-destructive"><strong>{resourceLabels[key as keyof typeof resourceLabels]}：</strong>{value}</p>)}<p className="text-[11px] text-muted-foreground">保留当前内容，服务恢复后会自动重连。</p></div><Button type="button" variant="ghost" size="icon-sm" aria-label="重新连接服务" disabled={data.refreshing} onClick={() => { void data.refresh() }}><RefreshCw className={cn("size-4", data.refreshing && "animate-spin")} /></Button></div>}
        {warnings.map(warning => <p key={warning} role="alert" className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-400">{warning}</p>)}
        {notice && <div role={notice.kind === "error" ? "alert" : "status"} className={cn("notice mb-5 flex items-start gap-3 rounded-xl border p-3.5 text-xs", notice.kind === "error" ? "border-destructive/20 bg-destructive/5 text-destructive" : "border-primary/15 bg-primary/5 text-primary")}>{notice.kind === "error" ? <AlertCircle className="size-4 shrink-0" /> : <CheckCircle2 className="size-4 shrink-0" />}<p className="min-w-0 flex-1 break-words leading-5">{notice.text}</p><button type="button" aria-label="关闭提示" onClick={() => setNotice(null)} className="shrink-0 rounded-sm p-0.5"><X className="size-3.5" /></button></div>}
        {view === "studio" && (data.options ? <StudioView draft={draft} update={update} onUpload={upload => setDraft(old => ({ ...old, upload }))} options={data.options} voices={data.voices} status={data.status} jobs={data.jobs} activeJob={activeJob} activeError={activeError} generating={generating} busy={busy} onGenerate={() => { void generate() }} onSelectVoice={useVoice} onSaveVoice={() => createVoice(draft)} onHistory={() => navigate("history")} onVoices={() => navigate("voices")} {...jobCallbacks} /> : <div className="flex min-h-96 flex-col items-center justify-center rounded-2xl border bg-card text-center">{data.loading ? <LoaderCircle className="mb-5 size-8 animate-spin text-primary" /> : <Radio className="mb-5 size-8 text-muted-foreground" />}<h1 className="text-xl font-semibold">{data.loading ? "正在准备声音工作室" : "等待连接本地服务"}</h1><p className="mt-3 max-w-sm px-4 text-sm leading-6 text-muted-foreground">连接后会载入模型支持的音色、语言与参数。你的草稿已保留。</p><Button className="mt-6" variant="outline" disabled={data.refreshing} onClick={() => { void data.refresh() }}><RefreshCw className={cn(data.refreshing && "animate-spin")} />重新连接</Button></div>)}
        {view === "voices" && <VoicesView voices={data.voices} loading={data.loading} busy={busy} onCreate={() => createVoice()} onEdit={voice => setEditor({ voice })} onUse={useVoice} onFavorite={voice => { void run(() => api.updateVoice(voice.id, { favorite: !voice.favorite }), voice.favorite ? "已取消收藏声音。" : "已收藏声音。") }} onDelete={voice => setConfirmation({ title: `删除声音「${voice.name}」？`, description: "该声音预设将被删除，已有的生成记录与参数快照会继续保留。", confirm: async () => { const result = await run(() => api.deleteVoice(voice.id), "声音预设已删除。"); if (result.ok && draft.params.voice_id === voice.id) update({ voice_id: undefined }); return result.ok } })} />}
        {view === "history" && <HistoryView jobs={data.jobs} loading={data.loading} busy={busy} {...jobCallbacks} />}
        <footer className="workspace-footer"><span className="flex items-center gap-1.5"><AudioLines className="size-3" />Ming 声音工作室</span><span>让每个字，都有温度。</span></footer>
      </main>
    </div>
    {editor && data.options && <VoiceEditor voice={editor.voice} seed={editor.seed} options={data.options} onClose={() => setEditor(null)} onSave={saveVoice} />}
    <ConfirmDialog value={confirmation} onClose={() => setConfirmation(null)} busy={busy} error={notice?.kind === "error" ? notice.text : undefined} />
    <Dialog open={!!rename} onOpenChange={open => { if (!open && !busy) setRename(null) }}><DialogContent><DialogHeader><DialogTitle>修改音频标题</DialogTitle><DialogDescription>为这次创作起一个容易找到的名字。</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); if (rename && renameTitle.trim()) void run(() => api.updateJob(rename.id, { title: renameTitle.trim() }), "标题已更新。").then(result => { if (result.ok) setRename(null) }) }}><Label htmlFor="job-title" className="mb-2">音频标题</Label><Input id="job-title" value={renameTitle} onChange={event => setRenameTitle(event.target.value)} required maxLength={120} autoFocus />{notice?.kind === "error" && <p role="alert" className="mt-3 text-xs text-destructive">{notice.text}</p>}<DialogFooter className="mt-5"><Button type="button" variant="outline" onClick={() => setRename(null)} disabled={busy}>取消</Button><Button type="submit" disabled={busy || !renameTitle.trim()}>{busy && <LoaderCircle className="animate-spin" />}保存标题</Button></DialogFooter></form></DialogContent></Dialog>
    <Dialog open={help} onOpenChange={setHelp}><DialogContent className="max-h-[85dvh] overflow-y-auto"><DialogHeader><DialogTitle className="flex items-center gap-2"><Sparkles className="size-5 text-primary" />开始声音创作</DialogTitle><DialogDescription>所有生成请求都由当前连接的本地服务处理。</DialogDescription></DialogHeader><div className="space-y-5 py-2 text-sm leading-7"><p><strong>01 · 选择声音</strong><br /><span className="text-muted-foreground">上传参考音频来克隆声音，或切换「内置音色」「声音设计」「自由指令」模式。克隆参考音频最长 {data.options?.limits.max_reference_seconds ?? 120} 秒。</span></p><p><strong>02 · 输入文本</strong><br /><span className="text-muted-foreground">输入或导入 UTF-8 文本，使用声音标签添加笑声、叹气等表达。每次最多 {data.options?.limits.max_text_length ?? 2000} 字。</span></p><p><strong>03 · 生成与收藏</strong><br /><span className="text-muted-foreground">点击生成，或按 ⌘ / Ctrl + Enter。排队时可以取消；生成过程中请等待模型完成。结果可试听、下载，也可以恢复参数继续编辑。</span></p><div className="space-y-2"><p className="text-sm font-medium">04 · 高级参数需要改吗？</p><p className="text-xs leading-6 text-muted-foreground">日常保持默认值即可。展开高级参数后，悬停或点击每项旁的问号查看说明；完整解释与常见问题也在参数手册中。</p>{data.options && <ParameterGuide options={data.options} />}</div></div><Badge variant="secondary" className="w-fit gap-1.5 text-xs"><Cpu className="size-3" />草稿保存在浏览器，声音与记录保存在服务中</Badge></DialogContent></Dialog>
  </div>
}
