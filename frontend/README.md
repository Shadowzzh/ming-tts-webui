# Ming 声音工作室前端

React 19 + TypeScript + Vite 7 + Tailwind CSS v4。界面为中文，支持浅色、深色与系统主题，字体全部来自本机系统。

## 本地开发

```sh
npm ci --include=dev
npm run dev
```

Vite 仅监听 `127.0.0.1`，默认入口 `http://127.0.0.1:5173`。`/api` 代理至本机 `http://127.0.0.1:17864`。前端没有演示模式、硬编码声音或生成记录，也不会访问外部推理服务。

```sh
npm run typecheck
npm run build
```

输出在 `dist/`，由 Python 服务同源提供。生产服务的静态目录应指向该目录；前端通过相对 `/api` 路径请求，不需要独立部署前端服务。

## 结构

- `src/types.ts`：API 合同。
- `src/lib/api.ts`：同源请求、超时与错误处理。
- `src/hooks/`：本地草稿、服务数据、任务轮询与自动重连。
- `src/views/`：创作工作台、声音库、生成记录。
- `src/components/`：参数编辑、参考音上传、预设编辑、音频播放及记录操作。
- `src/components/ui/`：通过官方 shadcn CLI 复制的 New York / Radix 组件；配置保留在 `components.json`。
- `src/index.css`：Tailwind v4 主题、系统字体及响应式布局。

shadcn 组件使用官方命令 `npx shadcn@latest add button card input textarea label select dialog alert-dialog switch badge separator dropdown-menu collapsible --yes` 获取。CLI 初次未识别项目引用中的别名，生成到 `@/components/ui`，已移入 `src/components/ui`，并将工具函数导入统一为 `@/lib/utils`；关闭按钮文本已汉化。现已在根 tsconfig 中声明别名，便于后续 CLI 使用。

## 行为说明

草稿、选定任务和主题以 `ming-studio.*` 键保存在浏览器 localStorage 中；写入失败会显示提示。参考音实际上传到后端，仅发送返回的 `upload_id`。

选择声音预设时载入完整参数、参考文字、上传标识并发送 `voice_id`。恢复历史和重试失败任务均复制生成时的参数快照，移除 `voice_id`，避免预设修改或删除后改变原有生成参数。

任务使用后端状态轮询，不估算生成百分比。只为排队任务显示取消操作，运行中的任务等待完成。删除声音或终态记录前必须确认。断开连接后保留草稿和已知记录，定时重连；服务恢复、页面恢复可见或浏览器恢复联网时继续刷新。

文本导入只接受有效 UTF-8 的 `.txt`，按 Unicode 码点检查字数。参考音支持 WAV / MP3 / FLAC / M4A / OGG，上传大小与时长采用选项接口中的限制。所有音频试听与下载均使用真实后端 URL。
