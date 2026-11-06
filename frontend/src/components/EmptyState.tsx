import type { ReactNode } from "react"
import { AudioLines } from "lucide-react"
export function EmptyState({ title, description, action, icon }: { title: string; description: string; action?: ReactNode; icon?: ReactNode }) {
  return <div className="flex flex-col items-center justify-center px-5 py-14 text-center"><div className="mb-5 flex size-14 items-center justify-center rounded-2xl border bg-background text-primary shadow-xs">{icon ?? <AudioLines className="size-6" />}</div><h3 className="text-sm font-medium">{title}</h3><p className="mt-2 max-w-72 text-xs leading-6 text-muted-foreground">{description}</p>{action && <div className="mt-5">{action}</div>}</div>
}
