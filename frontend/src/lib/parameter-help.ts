import type { Options } from "@/types"

export const numberParameters = [
  { key: "cfg", label: "引导强度 · cfg", step: 0.1, min: 0, max: 10 },
  { key: "sigma", label: "噪声系数 · sigma", step: 0.05, min: 0, max: 1 },
  { key: "temperature", label: "采样温度", step: 0.1, min: 0, max: 2 },
  { key: "max_length", label: "分段目标长度", step: 1, min: 1, max: 60 },
  { key: "max_decode_steps", label: "解码步数上限", step: 1, min: 50, max: 2000 },
  { key: "seed", label: "随机种子 · seed", step: 1, min: 0, max: 2147483647 },
] as const

// Verified against the deployed 0.5B MingTTS.synthesize, text_processing and
// fm/CFM.py. The inline help and the manual deliberately share this catalog.
export const parameterHelp = {
  cfg: {
    title: "引导强度",
    description: "控制生成时对文本、参考声音和演绎条件的引导力度。数值越大，条件引导越强，但不保证音色更像或音质更好。",
    advice: "日常保留默认值。需要对比时固定随机种子，以默认值为基准小幅调整；声音不自然时恢复默认。",
  },
  sigma: {
    title: "噪声系数",
    description: "控制采样过程中的额外噪声强度，与采样温度共同作用。当前模型在温度为 0 时，这部分噪声为 0，调整 sigma 不会增加这部分变化。",
    advice: "通常无需修改。只有把温度调高、想进一步控制变化程度时，再尝试小幅调整。",
  },
  temperature: {
    title: "采样温度",
    description: "控制额外采样噪声的随机程度。0 表示关闭这部分噪声；调高可能增加演绎变化，也可能降低稳定性。温度为 0 时，初始噪声仍受随机种子影响。",
    advice: "日常保留 0。想换一种读法可先换随机种子；仍需更多变化时，再小幅提高温度并试听。",
  },
  max_length: {
    title: "分段目标长度",
    description: "按标点组织文本片段时的目标语义长度，不是音频秒数，也不是整篇文本的字数上限。完整长分句可能超过目标，模型不会为了凑数强行截断分句。",
    advice: "通常保留默认值。想让文本分得更细可适当减小，并补好标点。开启「使用原始文本」后，此项不参与分段。",
  },
  max_decode_steps: {
    title: "解码步数上限",
    description: "每个文本片段最多允许生成多少个自回归解码步骤；模型可以提前结束。这不是音质档位，也不是扩散去噪步数，设置更大不代表声音更好。",
    advice: "日常保留默认值。长段没读完时，先检查标点与分段，再考虑适度提高上限；设置太低可能导致音频被截断。",
  },
  seed: {
    title: "随机种子",
    description: "控制随机数序列。同一文本、参考音、参数和运行环境下，固定种子便于复现与对比；更换种子可以尝试另一种演绎。跨设备或模型版本不保证完全一致。",
    advice: "比较不同设置时保持种子不变。只想再试一种读法时，可以换一个数字。",
  },
  use_spk_emb: {
    title: "使用参考说话人特征",
    description: "额外提取参考录音的说话人特征，辅助保留音色。关闭此项不等于关闭所有参考音提示；有准确参考文字时，仍可能使用参考录音的声学信息。与零说话人特征互斥。",
    advice: "克隆声音时建议保持开启。它不会训练新的模型，也不会自动让低质量录音变清晰。",
  },
  use_zero_spk_emb: {
    title: "使用零说话人特征",
    description: "在克隆模式中使用全零的说话人向量，与参考说话人特征互斥，此时也不使用上传参考音的声学提示。它不是降噪或零样本克隆开关。内置音色、声音设计和自由指令模式会自动处理此特征。",
    advice: "日常保持关闭。需要保留参考音色时不要开启；仅在有明确对照或排查目的时使用。",
  },
  no_trim_silence: {
    title: "保留末尾静音",
    description: "开启后保留每个生成片段末尾的静音；关闭时会裁短较长的尾部静音，也可能缩短拼接处的停顿。此项不处理开头静音，也不去除背景底噪。",
    advice: "日常保持关闭。需要保留模型原始结尾、后续自己剪辑，或比较原始停顿时再开启。",
  },
  audio_postprocess: {
    title: "轻度降噪",
    description: "在合成完成后轻度清理低频与底噪，并尽量保持整体音量。此选项可随声音预设保存，默认关闭；不会修改参考录音。",
    advice: "底噪明显时开启并试听。若尾音、齿音或音色受到影响，请关闭；它不能保证消除所有合成杂音。",
  },
  raw_text: {
    title: "使用原始文本",
    description: "绕过常规文本清理和自动分段，将整段原文直接送入模型。开启后「分段目标长度」不再生效，长文本更容易触及解码上限。",
    advice: "日常保持关闭。只有需要测试模型原始输入行为时再开启，建议先使用短文本。",
  },
} as const

export type ParameterKey = keyof typeof parameterHelp
export const parameterKeys = Object.keys(parameterHelp) as ParameterKey[]

export function parameterDefault(key: ParameterKey, options: Options) {
  if (key in options.defaults) return String(options.defaults[key as keyof Options["defaults"]])
  if (key === "use_spk_emb") return "开启（克隆模式）"
  return key === "use_zero_spk_emb" ? "关闭（克隆模式）" : "关闭"
}
