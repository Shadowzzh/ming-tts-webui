import { useId, type ReactNode } from "react"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
export function Field({ label, hint, help, children, className, htmlFor }: { label: string; hint?: string; help?: ReactNode; children: ReactNode; className?: string; htmlFor?: string }) {
  return <div className={cn("space-y-2", className)}><div className="flex min-h-4 items-center gap-1"><Label htmlFor={htmlFor} className="min-w-0 text-xs font-medium">{label}</Label>{help}</div>{children}{hint && <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}</div>
}
export function Choice({ label, value, onChange, items, placeholder = "请选择", hint, disabled, className }: { label: string; value?: string; onChange: (value: string) => void; items: { value: string; label: string }[]; placeholder?: string; hint?: string; disabled?: boolean; className?: string }) {
  const id = useId()
  return <Field label={label} hint={hint} htmlFor={id} className={className}><Select value={value || undefined} onValueChange={onChange} disabled={disabled}><SelectTrigger id={id} className="w-full bg-background"><SelectValue placeholder={placeholder} /></SelectTrigger><SelectContent>{items.map(item => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent></Select></Field>
}
