import { useEffect, useRef, useState } from "react"
import { BookOpen, CircleHelp } from "lucide-react"
import { Popover } from "radix-ui"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { parameterDefault, parameterHelp, parameterKeys, type ParameterKey } from "@/lib/parameter-help"
import type { Options } from "@/types"

export function ParameterHelp({ name, options }: { name: ParameterKey; options: Options }) {
  const [open, setOpen] = useState(false)
  const pinned = useRef(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const help = parameterHelp[name]
  function cancelClose() { clearTimeout(closeTimer.current) }
  function closeLater() {
    cancelClose()
    if (!pinned.current && !trigger.current?.matches(":focus-visible")) {
      closeTimer.current = setTimeout(() => setOpen(false), 180)
    }
  }
  useEffect(() => () => clearTimeout(closeTimer.current), [])
  return <Popover.Root open={open} onOpenChange={next => { cancelClose(); setOpen(next); if (!next) pinned.current = false }}>
    <Popover.Trigger asChild>
      <button ref={trigger} type="button" aria-label={`查看「${help.title}」说明`} className="inline-flex size-6 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onPointerEnter={event => { if (event.pointerType === "mouse") { cancelClose(); setOpen(true) } }}
        onPointerLeave={closeLater}
        onFocus={event => { if (event.currentTarget.matches(":focus-visible")) { cancelClose(); setOpen(true) } }}
        onClick={event => { event.preventDefault(); cancelClose(); pinned.current = !pinned.current; setOpen(pinned.current) }}>
        <CircleHelp aria-hidden="true" className="size-3.5" />
      </button>
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content aria-label={`${help.title}说明`} side="top" align="start" sideOffset={6} collisionPadding={12}
        onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()}
        onPointerEnter={cancelClose} onPointerLeave={closeLater}
        className="z-[60] w-80 max-w-[calc(100vw-1.5rem)] max-h-[var(--radix-popover-content-available-height)] overflow-y-auto rounded-xl border bg-popover p-4 text-popover-foreground shadow-xl outline-none">
        <p className="text-sm font-semibold">{help.title}</p>
        <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">{name} · 默认：{parameterDefault(name, options)}</p>
        <p className="mt-3 text-xs leading-6">{help.description}</p>
        <p className="mt-2 rounded-lg bg-muted/60 p-2.5 text-xs leading-6"><strong className="font-medium text-primary">怎么选：</strong>{help.advice}</p>
        <p className="mt-2 text-[10px] leading-5 text-muted-foreground">点击问号可固定说明，点击外部或按 Esc 关闭。</p>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>
}

export function ParameterGuide({ options }: { options: Options }) {
  return <Dialog>
    <DialogTrigger asChild><Button type="button" size="sm" variant="outline" className="gap-1.5 text-xs"><BookOpen className="size-3.5" />参数手册</Button></DialogTrigger>
    <DialogContent className="max-h-[85dvh] overflow-y-auto text-left sm:max-w-3xl">
      <DialogHeader>
        <DialogTitle>高级参数手册</DialogTitle>
        <DialogDescription>大多数时候保持默认值即可。每项参数旁的问号支持鼠标悬停、键盘聚焦或手机点击。</DialogDescription>
      </DialogHeader>
      <div className="rounded-xl border border-primary/15 bg-primary/5 p-4 text-xs leading-6">
        <p className="font-semibold text-primary">先用默认值，再一次只改一项</p>
        <p className="mt-1 text-muted-foreground">声音像不像，优先检查参考录音是否清晰、单人、无背景音乐，以及参考文字是否准确。语速、情绪和音调请在对应生成模式中选择。</p>
      </div>
      <div className="grid gap-3 text-xs leading-6 sm:grid-cols-2">
        <p className="rounded-lg bg-muted/60 p-3"><strong>想换一种读法</strong><br />先换随机种子；比较参数时把种子固定。</p>
        <p className="rounded-lg bg-muted/60 p-3"><strong>长段没读完</strong><br />补好标点、检查分段，保持原始文本关闭，再考虑提高解码上限。</p>
        <p className="rounded-lg bg-muted/60 p-3"><strong>想保留参考音色</strong><br />开启参考说话人特征，关闭零说话人特征。</p>
        <p className="rounded-lg bg-muted/60 p-3"><strong>句间停顿太长</strong><br />关闭保留末尾静音，并检查文本中的标点。</p>
      </div>
      <div className="divide-y">
        {parameterKeys.map(key => {
          const help = parameterHelp[key]
          return <section key={key} className="py-4 first:pt-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"><h3 className="text-sm font-semibold">{help.title}</h3><span className="text-xs text-muted-foreground">默认：{parameterDefault(key, options)}</span></div>
            <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">{key}</p>
            <p className="mt-2 text-xs leading-6">{help.description}</p>
            <p className="mt-1 text-xs leading-6 text-muted-foreground"><strong className="font-medium text-foreground">建议：</strong>{help.advice}</p>
          </section>
        })}
      </div>
    </DialogContent>
  </Dialog>
}
