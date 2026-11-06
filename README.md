# Ming 声音工作室

Ming-3.0-tts 的本地语音工作台。前端为 React、TypeScript、Tailwind CSS v4 和 shadcn/ui（New York / Radix）；后端为 Flask、Waitress、SQLite，模型继续使用现有 Ming GPU 环境。

本目录是应用源码真源。workhorse 的部署配置和运行手册位于 [`hosts/workhorse/ming-tts-web`](../../hosts/workhorse/ming-tts-web/README.md)，上游为 `Kodeer/ming-tts-web@630933be275c5f5c60234f13ddd475848a2f741d`（MIT）；保留其 `LICENSE`、选项定义和推理适配逻辑。

## 功能

- 创作工作台：中文界面，8 种任务、按模式显示参数、参考音上传与试听、UTF-8 文本导入、声音标签、高级参数、随机种子。
- 参数帮助：每项高级参数旁的问号支持悬停、聚焦和点击；显示默认值、用途与调整建议；高级参数和使用指南内都有完整手册。[文档版参数说明](../../docs/services/ming-tts-web/高级参数说明.md)。
- 声音库：保存克隆参考音、内置音色、声音设计；名称、备注、标签、收藏、编辑和删除。数据存服务端，各浏览器共享。
- 生成记录：文本、声音名称和完整参数快照；搜索、收藏、改标题、试听、下载、恢复参数、重试、确认删除。
- 持久任务队列：最多 12 个等待任务，单 worker 串行推理；排队可取消；运行中不强行中断。刷新或离开页面后继续执行。
- 重启处理：等待任务继续排队；中断的运行任务标记失败，保留参数供手动重试。跨进程文件锁禁止多个实例共用一个状态目录；SQLite 事务原子领取任务。
- 桌面与移动布局、深浅主题、浏览器草稿、`Ctrl/Meta+Enter` 提交。音频和预设保存在服务端，草稿仅在当前浏览器。

## 目录

| 路径 | 职责 |
| --- | --- |
| `frontend/src/views/` | 工作台、声音库、生成历史 |
| `frontend/src/components/ui/` | 通过官方 CLI 获取的 shadcn 组件 |
| `frontend/src/lib/parameter-help.ts` | 问号与页面手册共用的参数说明及默认值展示 |
| `app/server.py` | HTTP API、上传及同源页面服务 |
| `app/validation.py` | 参数、文件路径与声音预设校验 |
| `app/storage.py` | SQLite 元数据 |
| `app/jobs.py` | 任务生命周期、实例锁、串行执行 |
| `app/runtime.py` | 音频探测、随机种子、推理与 WAV 写入 |
| `app/tts_wrapper.py` | Ming 官方接口适配；懒加载模型 |
| `tests/test_studio.py` | 使用测试引擎验证 API、存储和并发，无需 Torch/GPU |
| `bundle.py` | 构建前端并按白名单打包发布目录 |

## 开发与验证

前端：

```bash
cd services/ming-tts-web/frontend
npm ci
npm run typecheck
npm run build
npm run dev
```

Vite 只监听 `127.0.0.1`，开发代理 `/api` 指向 `127.0.0.1:17864`。生产由 Waitress 同源提供构建后的静态文件，无 Node 常驻服务、CDN 或远端字体。

后端测试可以在独立临时环境执行，不安装 GPU 依赖：

```bash
uv venv /tmp/ming-studio-venv
uv pip install --python /tmp/ming-studio-venv/bin/python -r services/ming-tts-web/requirements-web.txt pytest==8.4.2
cd services/ming-tts-web
PYTHONPATH=. /tmp/ming-studio-venv/bin/pytest -q
```

测试引擎只验证接口、队列和存储行为，不能代替真实模型合成验收。生产运行方式是 `python -u -m app.server`，单进程 Waitress；实际 Torch、torchaudio 和官方 Ming 代码来自既有 GPU 基础镜像。

## 发布包

```bash
python3 services/ming-tts-web/bundle.py --output /tmp/ming-tts-studio-2.0.1.tar.gz
```

脚本执行 `npm ci` 与生产构建，并保存日志；打包只包含本应用 Python 文件、前端 `dist`、部署配置、许可、Web 依赖和 SHA256 manifest，不包含工作区其他文件、录音、数据库、测试、缓存或 `node_modules`。已验证构建可传 `--skip-build`。解压后的目录才能直接执行 `docker compose build`；配置副本目录本身没有完整构建上下文。

## API

错误返回 JSON `{"error":"说明"}`。所有写操作仅允许同源浏览器 Origin；服务仍按 LAN/Tailnet 单用户场景部署，无账号系统。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/options` | 模型选项、默认值、限制 |
| GET | `/api/status` | 模型/GPU、队列与存储计数 |
| POST | `/api/upload_prompt` | multipart `file`，返回参考音标识和元数据 |
| GET | `/api/uploads/<id>` | 参考音试听 |
| GET / POST | `/api/voices` | 列出 / 创建声音 |
| PATCH / DELETE | `/api/voices/<id>` | 编辑 / 删除声音预设 |
| GET / POST | `/api/jobs` | 搜索历史 / 提交异步任务（202） |
| GET / PATCH / DELETE | `/api/jobs/<id>` | 读取 / 收藏与标题 / 删除终态任务及音频 |
| POST | `/api/jobs/<id>/cancel` | 取消等待任务；运行中返回 409 |
| GET | `/api/audio/<file>` | WAV 播放，支持 Range |
| GET | `/api/download/<file>` | 下载 WAV |

旧版同步 `/api/synthesize` 已由异步 `/api/jobs` 替代，外部脚本需改为提交后轮询任务。任务快照不会重新解析声音预设；删除或修改预设后，历史重试仍使用原参考音和参数。

## 数据边界

每次最多 2000 字、参考音最长 120 秒、单请求最多 30 MiB。会检查音频可解码、数值范围、非有限数、路径穿越和符号链接；不接受客户端提供的服务器路径。预设没有保存训练后的独立模型，而是保存参考音或音色描述及参数，实际合成仍使用 Ming 模型。

删除声音仅删除预设元数据，参考音保留给历史快照；删除生成记录会同时删除对应输出音频。没有自动按天清理音频，避免破坏收藏或历史记录。手动维护时应先备份并核对引用关系。旧版生成的 WAV 没有文本和参数元数据，因此不会伪造成新版历史记录；原文件仍保留。

2000 字是输入限制，不代表已经验证任意长稿的完整性。`max_length` 是模型按标点组织文本时的目标语义长度；模型会保留完整长分句，`raw_text` 开启后绕过此分段。`max_decode_steps` 是每段自回归步骤上限，过低可能截断；`sigma` 的额外噪声项在温度 0 时为 0。应用未另外实现长稿编排、多人协作、声线训练或录音转写。
