import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"

// Run with: node --test frontend/tests/capabilities.test.mjs (no added dependencies).
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
let server, temporary, format, JobCard, App
before(async () => {
  temporary = await mkdtemp(join(tmpdir(), "ming-frontend-test-"))
  server = await createServer({
    root, cacheDir: temporary,
    server: { middlewareMode: true, hmr: false, watch: null },
    plugins: [{
      name: "stub-studio-data", enforce: "pre",
      transform(_source, id) {
        if (id.endsWith("/src/hooks/useStudioData.ts")) return `
          export function useStudioData() { return globalThis.__mingTestData }
          export function useActiveJob() { return { job: null, error: "" } }
        `
      },
    }],
  })
  format = await server.ssrLoadModule("/src/lib/format.ts")
  ;({ JobCard } = await server.ssrLoadModule("/src/components/JobCard.tsx"))
  ;({ default: App } = await server.ssrLoadModule("/src/App.tsx"))
})
after(async () => {
  await server?.close()
  if (temporary) await rm(temporary, { recursive: true, force: true })
  delete globalThis.__mingTestData
})

const job = (overrides = {}) => ({
  id: "test-job", status: "running", backend: "local", phase: null,
  title: "能力测试", text: "你好", voice_name: "测试声音", parameters: { task: "TTS" },
  created_at: "2026-01-01T00:00:00Z", started_at: null, finished_at: null,
  error: null, filename: null, audio_url: null, download_url: null,
  duration_seconds: null, sample_rate: null, elapsed_seconds: null, favorite: false,
  ...overrides,
})
const noop = () => {}
const renderCard = (item, extra = {}) => renderToStaticMarkup(createElement(JobCard, {
  job: item, onRestore: noop, onRetry: noop, onFavorite: noop, onDelete: noop, onCancel: noop, ...extra,
}))

for (const [name, fields, expected] of [
  ["queued", { status: "queued" }, "排队中"],
  ["waiting for execution", { backend: "ray", phase: "waiting_gpu" }, "排队中"],
  ["preparing", { phase: "starting" }, "准备中"],
  ["synthesizing", { phase: "running" }, "生成中"],
  ["local without progress", { phase: null }, "生成中"],
  ["legacy without phase", { phase: undefined }, "生成中"],
  ["stopping", { phase: "cancelling" }, "正在停止"],
]) test(`status label: ${name}`, () => assert.equal(format.statusLabel(job(fields)), expected))

test("local running message says generation is in progress", () => {
  assert.match(format.activeMessage(job()), /正在生成/)
  assert.doesNotMatch(format.activeMessage(job()), /等待.*进度|GPU|容器|Ray/)
})

test("phase messages use general terms without invented progress", () => {
  for (const fields of [{ status: "queued" }, { phase: "waiting_gpu" }, { phase: "starting" }, { phase: "running" }, { phase: "cancelling" }]) {
    const message = format.activeMessage(job(fields))
    assert.ok(message.length > 0)
    assert.doesNotMatch(message, /GPU|容器|Ray|执行器|\d+%/)
  }
})

for (const [name, fields, label] of [
  ["legacy queued", { status: "queued" }, "取消排队"],
  ["explicit queued", { status: "queued", can_cancel: true }, "取消排队"],
  ["local without capability", { can_cancel: false }, null],
  ["legacy local running", {}, null],
  ["legacy ray running", { backend: "ray" }, null],
  ["ray without capability", { backend: "ray", can_cancel: false }, null],
  ["local with capability", { can_cancel: true }, "停止生成"],
  ["ray with capability", { backend: "ray", can_cancel: true }, "停止生成"],
  ["explicit queued denial", { status: "queued", can_cancel: false }, null],
  ["stopping", { phase: "cancelling", can_cancel: true }, null],
  ["completed", { status: "completed", can_cancel: true }, null],
]) test(`cancel control: ${name}`, () => {
  const html = renderCard(job(fields))
  if (label) assert.match(html, new RegExp(`>${label}</button>`))
  else assert.doesNotMatch(html, />停止生成<|>取消排队</)
})

test("busy cancel control is disabled", () => {
  assert.match(renderCard(job({ can_cancel: true }), { busy: true }), /<button[^>]*disabled=""[^>]*>停止生成<\/button>/)
})

test("task cards omit backend diagnostics for every active phase", () => {
  for (const phase of [null, "waiting_gpu", "starting", "running", "cancelling"]) {
    const html = renderCard(job({ backend: "ray", phase, phase_detail: { executor_state: "diagnostic-marker" } }))
    assert.doesNotMatch(html, /Ray|GPU|容器|执行器|diagnostic-marker|\d+%/)
  }
})

test("timings use general names and only measured values", () => {
  assert.equal(format.timingsText(job({ timings: {
    queue_seconds: 1, container_seconds: 2, model_load_seconds: 3, inference_seconds: 4,
  } })), "排队 1.0 秒 · 准备环境 2.0 秒 · 模型加载 3.0 秒 · 合成 4.0 秒")
  for (const timings of [undefined, {}, { queue_seconds: -1, container_seconds: NaN, model_load_seconds: Infinity, inference_seconds: 0 }]) {
    assert.equal(format.timingsText(job({ timings })), "")
  }
})

test("sidebar presents generic service status with Ray diagnostics retained in the API", () => {
  globalThis.__mingTestData = {
    options: null, jobs: [], voices: [], errors: {}, loading: false, refreshing: false, refresh: noop,
    status: {
      backend: "ray", loaded: false, model_loading: "per-job", device: "ray/device-marker",
      ray: { profile: "profile-marker", reachable: true }, gpu: { name: "Ray GPU diagnostic-marker" },
      queue: { running: 1, queued: 2 }, storage: { jobs: 3, voices: 0 }, version: "test",
      capabilities: { cancel_running: true, reports_progress: true, resume_running: true },
    },
  }
  const html = renderToStaticMarkup(createElement(App))
  const sidebar = html.match(/<aside[\s\S]*?<\/aside>/)?.[0]
  assert.ok(sidebar)
  assert.match(sidebar, /服务已连接/)
  assert.doesNotMatch(sidebar, /Ray|GPU|容器|profile-marker|device-marker|diagnostic-marker/)
})
