import { useState } from "react"
import { AlertCircle } from "lucide-react"
export function AudioPlayer({ src, label }: { src: string; label: string }) {
  const [failed, setFailed] = useState(false)
  return <div className="space-y-2"><audio key={src} controls preload="none" src={src} aria-label={label} className="h-10 w-full min-w-0" onError={() => setFailed(true)} onCanPlay={() => setFailed(false)} />{failed && <p role="alert" className="flex gap-2 text-xs text-destructive"><AlertCircle className="size-4 shrink-0" />音频暂时无法播放，请检查连接后重新播放，或尝试下载。</p>}</div>
}
