import { useEffect, useState } from "react"
import { Heart, History, LoaderCircle, RefreshCw, Search } from "lucide-react"
import { api, message } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { EmptyState } from "@/components/EmptyState"
import { JobCard, type JobCallbacks } from "@/components/JobCard"
import { cn } from "@/lib/utils"
import type { Job, JobList } from "@/types"
export function HistoryView({ jobs, busy, loading, ...callbacks }: JobCallbacks & { jobs: Job[]; busy: boolean; loading: boolean }) {
  const [query, setQuery] = useState("")
  const [favorite, setFavorite] = useState(false)
  const [result, setResult] = useState<JobList | null>(null)
  const [error, setError] = useState("")
  const [searching, setSearching] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let cancelled = false
    setSearching(true)
    const timer = window.setTimeout(() => {
      void api.jobs(query.trim(), favorite).then(value => { if (!cancelled) { setResult(value); setError("") } }).catch(error => { if (!cancelled) setError(message(error)) }).finally(() => { if (!cancelled) setSearching(false) })
    }, 250)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [query, favorite, jobs, revision])
  const items = result?.items ?? (!query && !favorite ? jobs : [])
  return <div className="space-y-7">
    <div><p className="eyebrow">每一次创作，都值得留存</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">生成记录<span className="text-primary">。</span></h1><p className="mt-3 text-sm text-muted-foreground">回听、收藏和下载作品，也可以拾起上一次的灵感继续创作。</p></div>
    <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-card p-3"><div className="relative min-w-48 flex-1"><Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input value={query} onChange={event => setQuery(event.target.value)} aria-label="搜索生成记录" placeholder="搜索标题、文本或声音名称…" className="border-0 bg-muted/50 pl-9 shadow-none" /></div><Button type="button" size="sm" variant={favorite ? "secondary" : "ghost"} className={cn("text-xs", favorite && "text-primary")} onClick={() => setFavorite(value => !value)} aria-pressed={favorite}><Heart className={cn("size-3.5", favorite && "fill-primary")} />只看收藏</Button><Button type="button" size="icon-sm" variant="ghost" aria-label="刷新生成记录" onClick={() => setRevision(value => value + 1)} disabled={searching}><RefreshCw className={cn("size-4", searching && "animate-spin")} /></Button></div>
    <div className="flex items-center justify-between text-xs text-muted-foreground"><span className="flex items-center gap-2">{searching && <LoaderCircle className="size-3 animate-spin" />}{searching ? "正在更新记录…" : `共 ${result?.total ?? jobs.length} 条记录`}</span><span className="text-[11px]">最新生成优先 · 最多显示 100 条</span></div>
    {error && <p role="alert" className="rounded-xl border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive">{error}</p>}
    {items.length ? <div className="grid gap-4 xl:grid-cols-2" aria-busy={searching}>{items.map(job => <JobCard key={job.id} job={job} busy={busy} {...callbacks} />)}</div> : <div className="rounded-2xl border bg-card"><EmptyState icon={<History className="size-6" />} title={loading || searching ? "正在读取生成记录" : query || favorite ? "没有找到匹配的作品" : "这里将收藏你的创作旅程"} description={query || favorite ? "尝试更换关键词，或关闭收藏筛选。" : "在创作工作台生成第一段语音，音频与生成参数会自动保存在这里。"} /></div>}
  </div>
}
