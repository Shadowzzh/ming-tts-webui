# Ming 声音工作室

Ming-3.0-tts 0.5B 的语音工作台，支持声音预设、生成历史和参数帮助。前端使用 React、TypeScript、Tailwind CSS v4 和 shadcn/ui，后端使用 Flask、Waitress 与 SQLite。

**默认直接使用本机 NVIDIA GPU，独立构建和运行，无需 Ray。** 同一套应用也提供可选的远程执行适配；个人主机的地址、网络和调度配置由部署方管理。

## 功能

- 工作台：8 种生成模式、参考音上传与试听、文本导入、声音标签、随机种子和高级参数。
- 声音库：保存克隆参考音、内置音色或声音设计，以及名称、备注、标签、收藏和默认参数。
- 生成历史：试听、WAV 下载、搜索、收藏、改标题、恢复参数、重试和确认删除。
- 参数帮助：问号支持鼠标悬停、键盘聚焦和手机点击；页面内提供完整手册，也可读[参数说明](docs/参数说明.md)。
- 持久任务队列：最多 12 个等待任务，单 worker 串行生成；刷新或离开网页后任务继续。
- 桌面与手机布局、深浅主题、浏览器草稿、`Ctrl/Meta+Enter` 提交。

声音预设保存参考音或描述及参数，不是训练一个新模型。各浏览器共享服务端声音与历史，草稿保存在当前浏览器。

## 独立部署

支持 Linux x86_64 + NVIDIA GPU。需要 Docker Engine 28.3 或更新版本、Docker Compose，以及已生成 NVIDIA CDI 设备说明的 NVIDIA Container Toolkit。宿主只需 GPU 驱动与容器工具，Python、Node 和 CUDA 用户态依赖由镜像提供。构建会访问 Docker Hub、npm、PyPI、PyTorch 和 GitHub。

先确认宿主 `nvidia-smi` 正常，并运行：

```bash
nvidia-ctk cdi list
docker run --rm --device nvidia.com/gpu=all ubuntu:24.04 nvidia-smi
```

设备列表应包含 `nvidia.com/gpu=all`。CDI 的准备方式见 [NVIDIA 官方文档](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)，Docker 的版本要求见 [CDI 文档](https://docs.docker.com/reference/cli/dockerd/#configure-cdi-devices)。本项目不修改宿主 Docker 或驱动配置。

在仓库根目录执行：

```bash
cp .env.example .env
docker compose build
docker compose run --rm --no-deps \
  -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 \
  --entrypoint python ming-tts-web -c \
  "from huggingface_hub import snapshot_download; snapshot_download('inclusionAI/Ming-3.0-tts-0.5b', revision='7247f4c94a5b2e987404ed28a5cfec41b1a4ca61', local_dir='/models/Ming-3.0-tts-0.5B')"
docker compose up -d --no-build
```

打开 <http://127.0.0.1:17863/>。第一次生成时加载模型，之后由当前进程复用；停止或重建容器后显存释放。模型完整快照约 3 GB，镜像还包含 PyTorch/CUDA 运行依赖；构建和模型需要分别预留磁盘。显存需求随参考音与文本变化，8 GB 可作为准备配置的起点，实际峰值需在目标显卡验证。

下载失败可以重跑同一条命令，Hugging Face 会复用已下载文件。模型来自[官方发布](https://huggingface.co/inclusionAI/Ming-3.0-tts-0.5B)；推理源码固定为 `inclusionAI/Ming-3.0-tts@a39b80660bdc5a4394d5e1d41af6dbfd9c443a54`，构建时验证源码压缩包 SHA256。模型和推理源码遵循各自上游许可。

`.env` 可调整以下部署选项：

| 字段 | 默认值 | 用途 |
| --- | --- | --- |
| `MING_HOST_PORT` | `17863` | 宿主网页端口 |
| `MING_BIND_ADDRESS` | `127.0.0.1` | 网页监听地址；局域网访问可显式改为 `0.0.0.0` |
| `MING_GPU_DEVICE` | `nvidia.com/gpu=all` | CDI 设备；指定一张卡可填 `nvidia.com/gpu=0` |
| `MING_IMAGE` | `ming-tts-webui:2.1.2` | 本地构建后的镜像标签 |
| `WEB_ASSETS` | `frontend` | 源码构建前端；发布包的示例配置使用 `bundled` |

默认 Compose 明确选择 `MING_BACKEND=local`，没有外部网络、调度地址、Ray 安装步骤或其他项目的镜像。模型使用 PyTorch SDPA，不依赖 FlashAttention 或可选的 pynini 文本标准化。

## 数据、启停与备份

Compose 创建四个持久卷：`models`、`uploads`、`outputs`、`state`，实际名称带 Compose 项目前缀。分别保存模型、参考音、生成音频、预设与任务数据库。容器使用 UID 1000 非 root 用户。

```bash
docker compose ps
docker compose logs --tail 100 ming-tts-web
docker compose stop
docker compose up -d --no-build
```

Docker 日志按 10 MiB × 3 轮转。历史与音频不会自动过期；删除声音只删除预设元数据，保留参考音供历史重试；删除生成记录会删除对应音频。备份时先等生成结束并停止服务，再归档四个卷；恢复时保留数据库、参考音和输出之间的对应关系。模型卷可由同一固定版本重新下载。`docker compose down` 保留卷，`down -v` 会删除数据。

本地模式只允许取消排队任务；运行中的合成需等待结束。中断本地进程后，正在运行的任务标记失败，参数保留供手动重试；等待任务继续排队。跨进程文件锁阻止多个实例同时使用同一状态目录，运行时不要删除锁文件。

## 可选远程执行

已具备兼容的 Ray Jobs 与 GPU 执行器时，可以显式设置 `MING_BACKEND=ray`，让同一个网页应用接入共享 GPU。此模式不安装 Ray Python SDK；网页通过标准库 HTTP 客户端提交任务。GPU、模型、共享目录和执行器由部署侧提供，**单独启动一个普通 Ray 集群不足以完成接入**。

配置、共享文件协议、取消和恢复边界见[可选 Ray 后端](docs/ray.md)。默认 Compose 始终保持本地模式，远程模式使用部署方单独维护的 Compose。

普通任务页面显示“排队中、准备中、生成中、正在停止”等状态。服务通过 `capabilities` 和任务的 `can_cancel` 报告实际能力；前端据此显示操作。Ray 的 submission ID 等诊断信息保留在 API 内，普通任务卡无需依赖调度技术。

## 开发与验证

前端需要 Node.js 22：

```bash
cd frontend
npm ci
npm run typecheck
npm run build
npm run dev
```

Vite 默认监听 `127.0.0.1`，开发代理目标见 `frontend/vite.config.ts`。生产静态文件由 Waitress 同源提供，无 Node 常驻进程、CDN 或远端字体。

后端测试不需要 GPU、Torch 或 Ray：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt pytest==8.4.2
.venv/bin/python -m pytest -q
```

真实本地运行需要 Dockerfile 中的 Ming 源码与模型依赖。HTTP 入口为 `python -u -m app.server`，单进程 Waitress。API 测试使用测试引擎；Ray 协议测试使用进程内假执行器，不能代替真实 GPU、挂载和进程回收验收。

2.1.2 显式处理 SIGTERM/SIGINT：收到停止信号后不再领取任务或接受新提交（返回 503），Waitress 收尾后最多等待队列线程 5 秒。重复停止信号不会跳过清理。默认 Compose 继续使用 `init: true`；忙碌 HTTP 请求与原生库退出可能增加实际停机时间，5 秒不是生产硬上限。本地模式中未完成的生成在重启时标为失败、等待任务继续；可选 Ray 模式的远端任务保持执行，重启后重连同一个 submission，不因停止网页而取消或重复提交。

| 路径 | 职责 |
| --- | --- |
| `app/server.py`、`app/validation.py` | HTTP API、上传、路径和参数校验 |
| `app/storage.py`、`app/jobs.py` | SQLite、队列与任务生命周期 |
| `app/engines.py`、`app/runtime.py` | 选择执行后端、默认本地 GPU 推理 |
| `app/ray_engine.py`、`app/gpu_dispatch.py` | 可选的业务适配和通用 HTTP 调度客户端 |
| `app/infer_job.py` | 远程 GPU 单任务入口 |
| `frontend/src/lib/parameter-help.ts` | 问号与页面内手册共用的参数解释 |
| `Dockerfile`、`compose.yaml` | 独立构建与默认部署 |
| `bundle.py` | 前端构建与白名单发布包 |

## 发布包

在源码仓库根目录执行：

```bash
python3 bundle.py --output /tmp/ming-tts-webui-2.1.2.tar.gz
```

脚本运行 `npm ci` 和生产构建，默认日志目录在系统临时目录的 `ming-tts-webui-build-logs/`，可用 `--log-dir` 指定。已验证的 `frontend/dist` 可使用 `--skip-build`。

发布包包含应用、预编译静态资源、仓库内 Dockerfile/Compose、依赖、许可、部署说明和逐文件 SHA256 manifest；不打包录音、模型、数据库、缓存或仓库外的配置。包内 `.env.example` 设置 `WEB_ASSETS=bundled`，解压后复制为 `.env` 即可沿用上述部署命令，Docker 构建会直接使用静态资源。源码构建与发布包共用同一份推理环境定义。

自定义部署可使用发布包中的应用和静态资源，但主机地址、GPU 池、网络、目录与个人基础镜像应留在部署仓库中。

## API 与限制

错误返回 JSON `{"error":"说明"}`，任务提交后通过 ID 轮询。服务按单用户场景设计，写操作校验同源 Origin，没有多用户账号系统。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/options`、`/api/status` | 模型选项、默认值、能力、模型/GPU与队列状态 |
| POST | `/api/upload_prompt` | multipart `file`，返回参考音标识 |
| GET | `/api/uploads/<id>` | 参考音试听 |
| GET / POST | `/api/voices` | 列出 / 创建声音 |
| PATCH / DELETE | `/api/voices/<id>` | 编辑 / 删除声音预设 |
| GET / POST | `/api/jobs` | 查询历史 / 提交异步任务（202） |
| GET / PATCH / DELETE | `/api/jobs/<id>` | 读取 / 收藏、标题 / 删除终态任务及音频 |
| POST | `/api/jobs/<id>/cancel` | 根据 `can_cancel` 取消；不支持的状态返回 409 |
| GET | `/api/audio/<file>`、`/api/download/<file>` | WAV 播放与下载，支持 Range |

每次最多 2000 字、参考音最长 120 秒、请求上限 30 MiB；检查音频可解码、非有限数、路径穿越和符号链接，不接受客户端提供服务器文件路径。任务保存实际参数快照，之后修改预设不会改变旧记录。

2000 字不是任意长稿完整性的保证。Ming 按标点和分段目标组织文本，`max_length` 不是秒数，`max_decode_steps` 是每段自回归步数上限；具体见参数手册。尚未实现额外长稿编排、多人协作、声线训练或参考音自动转写。

## 来源

应用基于 [Kodeer/ming-tts-web](https://github.com/Kodeer/ming-tts-web) 的选项定义与推理适配逻辑，保留 MIT `LICENSE`。模型与推理代码来自 [inclusionAI/Ming-3.0-tts](https://github.com/inclusionAI/Ming-3.0-tts)。
