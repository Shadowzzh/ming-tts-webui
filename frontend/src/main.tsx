import { StrictMode, Component, type ErrorInfo, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import App from "@/App"
import "@/index.css"
class ErrorBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: "" }
  static getDerivedStateFromError(error: Error) { return { error: error.message } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("工作室界面错误", error, info.componentStack) }
  render() { return this.state.error ? <div role="alert" className="mx-auto mt-20 max-w-lg space-y-5 rounded-xl border bg-card p-8"><h1 className="text-xl font-semibold">界面暂时无法显示</h1><p className="break-words text-sm text-muted-foreground">{this.state.error}</p><p className="text-sm text-muted-foreground">草稿仍保存在此浏览器中，请重新载入页面后继续。</p><button className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground" onClick={() => location.reload()}>重新载入</button></div> : this.props.children }
}
createRoot(document.getElementById("root")!).render(<StrictMode><ErrorBoundary><App /></ErrorBoundary></StrictMode>)
