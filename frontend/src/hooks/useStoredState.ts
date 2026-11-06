import { useCallback, useState, type SetStateAction } from "react"
export function useStoredState<T>(key: string, fallback: T, validate?: (value: unknown) => value is T) {
  const [initial] = useState(() => {
    try { const raw = localStorage.getItem(key); if (!raw) return { value: fallback, warning: "" }; const parsed: unknown = JSON.parse(raw); if (validate && !validate(parsed)) throw new Error(); return { value: parsed as T, warning: "" } }
    catch { return { value: fallback, warning: "本地草稿读取失败，已使用初始设置。请检查浏览器存储权限。" } }
  })
  const [value, setValue] = useState<T>(initial.value)
  const [warning, setWarning] = useState(initial.warning)
  const update = useCallback((next: SetStateAction<T>) => {
    setValue(previous => {
      const resolved = typeof next === "function" ? (next as (old: T) => T)(previous) : next
      try { localStorage.setItem(key, JSON.stringify(resolved)) } catch { queueMicrotask(() => setWarning("浏览器无法保存草稿，当前内容仅保留在此页面中。")) }
      return resolved
    })
  }, [key])
  return [value, update, warning] as const
}
