import { useId, useState, type ReactNode } from "react"
import { ChevronDown, RotateCcw, SlidersHorizontal } from "lucide-react"
import { Choice, Field } from "@/components/fields"
import { ParameterGuide, ParameterHelp } from "@/components/ParameterHelp"
import { numberParameters } from "@/lib/parameter-help"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { builtinLabels, dialectLabels, languageLabels, taskLabels } from "@/lib/format"
import type { Options, Params, TaskOption, TaskValue } from "@/types"
export function ParameterFields({ params, update, options, taskChoices, hideTask = false }: { params: Params; update: (value: Partial<Params>) => void; options: Options; taskChoices?: TaskOption[]; hideTask?: boolean }) {
  const id = useId()
  const task = options.tasks.find(item => item.value === params.task)
  const controls = task?.controls ?? []
  function changeTask(value: string) {
    update({ task: value as TaskValue, voice_id: undefined, use_zero_spk_emb: false, use_spk_emb: true })
  }
  return <div className="space-y-4">
    {!hideTask && <Choice label="生成模式" value={params.task} onChange={changeTask} items={(taskChoices ?? options.tasks).map(item => ({ value: item.value, label: taskLabels[item.value] ?? item.label }))} />}
    <Choice label={task?.language_optional ? "输出语言" : "输出语言 *"} value={params.language_id || "__auto"} onChange={value => update({ language_id: value === "__auto" ? undefined : value, voice_id: undefined })} items={[...(task?.language_optional ? [{ value: "__auto", label: "自动识别 · 中文 / 英语" }] : [{ value: "__auto", label: "请选择目标语言" }]), ...options.languages.map(item => ({ value: item.value, label: languageLabels[item.value] ?? item.label }))]} />
    {controls.includes("ip") && <Field label="内置音色 *" htmlFor={`${id}-ip`} hint="可选择推荐音色，也可输入模型音色目录中的名称。"><Input id={`${id}-ip`} list={`${id}-voices`} maxLength={100} value={params.ip ?? ""} placeholder="选择或输入音色名称" onChange={event => update({ ip: event.target.value, voice_id: undefined })} /><datalist id={`${id}-voices`}>{options.builtin_voices.map(item => <option key={item.value} value={item.value}>{builtinLabels[item.value] ?? item.value}</option>)}</datalist></Field>}
    {controls.includes("instruction") && <Field label="演绎指令 *" htmlFor={`${id}-instruction`} hint="描述语气、节奏或演绎方式。"><Textarea id={`${id}-instruction`} maxLength={1000} value={params.instruction ?? ""} onChange={event => update({ instruction: event.target.value, voice_id: undefined })} placeholder="例如：用轻松亲切的语气，像和朋友聊天一样。" /></Field>}
    {controls.includes("style") && <Field label="声音描述 *" htmlFor={`${id}-style`} hint="描述音色、年龄感、声线和声音质感。"><Textarea id={`${id}-style`} maxLength={1000} value={params.style ?? ""} onChange={event => update({ style: event.target.value, voice_id: undefined })} placeholder="例如：温暖、清晰的成年女声，语气自然，有轻微的气声。" /></Field>}
    {controls.includes("emotion") && <Choice label="情绪 *" value={params.emotion} onChange={value => update({ emotion: value, voice_id: undefined })} items={options.emotions.map(item => ({ ...item, label: item.value }))} />}
    {controls.includes("dialect") && <Choice label="方言 *" value={params.dialect} onChange={value => update({ dialect: value, voice_id: undefined })} items={options.dialects.map(value => ({ value, label: dialectLabels[value] ?? value }))} />}
    {controls.includes("speed") && <div className="grid grid-cols-3 gap-3">{(["speed", "pitch", "volume"] as const).filter(key => controls.includes(key)).map(key => <Choice key={key} label={{ speed: "语速 *", pitch: "音调 *", volume: "音量 *" }[key]} value={params[key]} onChange={value => update({ [key]: value, voice_id: undefined })} items={options[key].map(item => ({ ...item, label: item.value }))} />)}</div>}
  </div>
}
export function AdvancedParameters({ params, update, options }: { params: Params; update: (value: Partial<Params>) => void; options: Options }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const task = options.tasks.find(item => item.value === params.task)
  return <Collapsible open={open} onOpenChange={setOpen} className="border-t pt-4">
    <CollapsibleTrigger asChild><Button variant="ghost" type="button" className="h-auto w-full justify-between px-0 py-1 text-xs hover:bg-transparent"><span className="flex items-center gap-2"><SlidersHorizontal className="size-3.5" />高级参数</span><ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} /></Button></CollapsibleTrigger>
    <CollapsibleContent className="space-y-4 pt-4">
      <div className="rounded-xl bg-muted/50 p-3"><p className="text-xs font-medium">日常使用，保持默认值即可</p><p className="mt-1 text-[11px] leading-5 text-muted-foreground">不确定是否要改？悬停或点击问号查看用途与建议。比较效果时，一次只改一个参数。</p><div className="mt-2"><ParameterGuide options={options} /></div></div>
      <div className="grid grid-cols-2 gap-3">{numberParameters.map(item => <Field key={item.key} htmlFor={`${id}-${item.key}`} label={item.label} help={<ParameterHelp name={item.key} options={options} />}><Input id={`${id}-${item.key}`} type="number" step={item.step} min={item.min} max={item.max} value={params[item.key] ?? options.defaults[item.key]} onChange={event => update({ [item.key]: event.target.value === "" ? undefined : Number(event.target.value), voice_id: undefined })} /></Field>)}</div>
      {params.raw_text && <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-2.5 text-[11px] leading-5 text-amber-700 dark:text-amber-400">已使用原始文本：整段直接输入模型，分段目标长度暂不生效。</p>}
      {task?.needs_prompt && <div className="space-y-3"><Toggle id={`${id}-speaker`} label="使用参考说话人特征" help={<ParameterHelp name="use_spk_emb" options={options} />} hint="克隆声音建议开启，辅助保留参考音色。" checked={params.use_spk_emb ?? true} onChange={checked => update({ use_spk_emb: checked, ...(checked ? { use_zero_spk_emb: false } : {}), voice_id: undefined })} /><Toggle id={`${id}-zero`} label="使用零说话人特征" help={<ParameterHelp name="use_zero_spk_emb" options={options} />} hint="实验选项，日常关闭；与参考说话人特征互斥。" checked={params.use_zero_spk_emb ?? false} onChange={checked => update({ use_zero_spk_emb: checked, ...(checked ? { use_spk_emb: false } : {}), voice_id: undefined })} /></div>}
      <Toggle id={`${id}-silence`} label="保留末尾静音" help={<ParameterHelp name="no_trim_silence" options={options} />} hint="日常关闭，自动裁短每段末尾的静音。" checked={params.no_trim_silence ?? false} onChange={checked => update({ no_trim_silence: checked, voice_id: undefined })} />
      <Toggle id={`${id}-raw`} label="使用原始文本" help={<ParameterHelp name="raw_text" options={options} />} hint="日常关闭，开启后跳过文本清理与自动分段。" checked={params.raw_text ?? false} onChange={checked => update({ raw_text: checked, voice_id: undefined })} />
      <Button type="button" variant="outline" size="sm" className="w-full text-xs" onClick={() => update({ ...options.defaults, no_trim_silence: false, raw_text: false, use_spk_emb: true, use_zero_spk_emb: false, voice_id: undefined })}><RotateCcw className="size-3" />恢复模型默认参数</Button>
    </CollapsibleContent>
  </Collapsible>
}
function Toggle({ id, label, hint, help, checked, onChange }: { id: string; label: string; hint?: string; help?: ReactNode; checked: boolean; onChange: (value: boolean) => void }) {
  return <div className="flex items-center justify-between gap-4"><div className="min-w-0 space-y-1"><div className="flex items-center gap-1"><Label htmlFor={id} className="text-xs">{label}</Label>{help}</div>{hint && <p className="text-[11px] leading-5 text-muted-foreground">{hint}</p>}</div><Switch id={id} checked={checked} onCheckedChange={onChange} /></div>
}
