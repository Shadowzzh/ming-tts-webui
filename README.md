# Ming 声音工作室

Ming-3.0-tts 的本地语音工作台。前端为 React、TypeScript、Tailwind CSS v4 和 shadcn/ui（New York / Radix）；后端为 Flask、Waitress、SQLite，模型继续使用现有 Ming GPU 环境。

本目录是应用源码真源。workhorse 的部署配置和运行手册位于 [`hosts/workhorse/ming-tts-web`](../../hosts/workhorse/ming-tts-web/README.md)，上游为 `Kodeer/ming-tts-web@630933be275c5f5c60234f13ddd475848a2f741d`（MIT）；保留其 `LICENSE`、选项定义和推理适配逻辑。

## 功能

- 创作工作台：中文界面，8 种任务、按模式显示参数、参考音上传与试听、UTF-8 文本导入、声音标签、高级参数、随机种子。
- 参数帮助：每项高级参数旁的问号支持悬停、聚焦和点击；显示默认值、用途与调整建议；高级参数和使用指南内都有完整手册。[文档版参数说明](../../docs/services/ming-tts-web/高级参数说明.md)。
- 声音库：保存克隆参考音、内置音色、声音设计；名称、备注、标签、收藏、编辑和删除。数据存服务端，各浏览器共享。
- 生成记录：文本、声音名称和完整参数快照；搜索、收藏、改标题、试听、下载、恢复参数、重试、确认删除。
- 持久任务队列：最多 12 个等待任务，单 worker 串行推理；排队可取消；本地后端运行中不强行中断，Ray 后端会真正停止 GPU 任务。刷新或离开页面后继续执行。
- 重启处理：等待任务继续排队；本地后端中断的运行任务标记失败，保留参数供手动重试；Ray 后端按同一任务标识重新附着。跨进程文件锁禁止多个实例共用一个状态目录；SQLite 事务原子领取任务。
- 可选 Ray 后端：`MING_BACKEND=ray` 时网页容器不挂载 `devices`、不加载模型，每个任务交给共享单卡执行器；等待 GPU / 准备模型 / 推理三段耗时分别记录。默认仍为容器内本地推理。
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
| `app/jobs.py` | 任务生命周期、实例锁、串行执行、Ray 进度与取消 |
| `app/engines.py` | 按 `MING_BACKEND` 选择后端（`local` / `ray`） |
| `app/ray_engine.py` | Ray 后端适配：请求映射、`/inputs` 参考音、结果回填、耗时阶段 |
| `app/infer_job.py` | GPU 容器 worker CLI：`python -m app.infer_job /job` |
| `app/gpu_dispatch.py` | Ray Jobs HTTP 客户端（纯标准库），与 `hosts/workhorse/ray-gpu/runtime/gpu_dispatch.py` 逐字节一致 |
| `app/runtime.py` | 音频探测、随机种子、推理与 WAV 写入 |
| `app/tts_wrapper.py` | Ming 官方接口适配；懒加载模型 |
| `tests/test_studio.py` | 使用测试引擎验证 API、存储和并发，无需 Torch/GPU |
| `tests/test_ray_backend.py` | 假 Ray / 假执行器验证调度协议、取消、断连、重启幂等与路径映射 |
| `bundle.py` | 构建前端并按白名单打包发布目录 |

## 可选 Ray 推理后端

默认 `MING_BACKEND=local`（原行为、原数据、原镜像）不变。设 `MING_BACKEND=ray` 后，网页容器只做参数校验、CPU 音频探测和任务编排，模型只在一次性 GPU 容器里按任务加载。

### 网页容器需要的配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MING_BACKEND` | `local` | `ray` 启用共享执行器 |
| `RAY_JOBS_URL` | `http://ray-gpu-control:8265` | 只能是私有 Docker 网络地址；Ray 的 8265 不对外发布 |
| `MING_RAY_PROFILE` | `ming` | 固定 profile，决定容器挂载与 entrypoint |
| `GPU_DISPATCH_DIR` | `/app/dispatch` | 与执行器共享的任务目录，必须与宿主 dispatch 目录同一挂载 |
| `MING_INPUTS_DIR` | `/inputs` | 参考音只读挂载；上传目录必须同时挂到这里 |
| `MING_RAY_TIMEOUT_SECONDS` | `3600` | 单任务总等待上限，超时会停止 Ray 任务 |
| `MING_RAY_POLL_SECONDS` | `1.0` | 轮询间隔；单次 HTTP 超时固定 ≤5 秒 |

Compose 部署步骤与 profiles 由 `hosts/workhorse/ray-gpu/` 维护。网页服务需要：

| 项 | 值 |
| --- | --- |
| 网络 | 加入外部网络 `ray-gpu-control`（Ray 的 8265 只发布在宿主回环，容器必须同网才能访问） |
| `RAY_JOBS_URL` | `http://ray-gpu-control:8265` |
| `GPU_DISPATCH_DIR` → 宿主 | `/app/dispatch` → `${OPS_DATA_ROOT}/ming-tts-web-data/dispatch`（执行器在 `/dispatch/ming` 看到同一目录） |
| `MING_INPUTS_DIR` → 宿主 | `/inputs` → `${OPS_DATA_ROOT}/ming-tts-web-data/uploads`，只读 |
| GPU | 网页容器删除 `devices: nvidia.com/gpu=0`；GPU 只给一次性模型容器 |

模型镜像沿用 `local/ming-tts-web:2.1.0-ray1`（同一镜像既做网页也做 worker，`/app` 下有 `app` 包，`working_dir=/app`）。模型目录仍只读挂给 GPU 容器，`/job` 由执行器注入，模型容器 `network_mode=none`。

### 任务协议与行为

- 任务目录 `<dispatch>/<job_id>/`：`request.json`（网页写入的业务 JSON，含 `parameters`）、`result.wav` / `result.json`（worker 原子写）、`executor.json`（执行器在容器退出后写入）、`dispatcher.json`（调度状态、submission、失败原因，供重附着和排障）。
- `submission_id = ming-<job_id>`，entrypoint 固定为 `python -m gpu_runner --profile ming --job-id <job_id>`，请求 1 张 GPU、0.1 CPU；音频不经 Ray 对象存储。
- 进度阶段：`waiting_gpu`（Ray PENDING，排在别的任务后面）→ `starting`（容器启动中）→ `running`（GPU 执行中，包含容器内的模型加载）；网页在用户取消期间把任务阶段显示为 `cancelling`。
- 耗时只统计有证据的部分：`queue_seconds`（`executor.json` 的 `created_at` − `dispatcher.json` 的 `submitted_at`）、`container_seconds`（`started_at` − `created_at`）、`model_load_seconds` / `inference_seconds`（worker 在 `result.json` 里显式记录，冷加载不算推理）、`total_seconds`（`finished_at` − `submitted_at`）。`starting` 窗口常短于轮询间隔而观测不到，因此不按“观察到哪个阶段”计算，缺失项直接不显示，不会用本地轮询时间猜。
- `started_at` 对 Ray 任务取执行器的容器启动时间，不再使用本地排队领取时间；本地后端保持原有领取时间。
- 通用 Dispatcher 的成功条件是：`executor.json` 为 `succeeded` 且 `result.json` 是非空 JSON 对象，不强制任何音频文件或 schema（Qwen 创建音色只产出 `prompt.pt` 也成立）。只看到 WAV 不会当作完成。Ming 在自己的 `RayEngine.generate` 里额外要求 `result.wav` 大于 44 字节，音频校验属于业务层。产物完整时允许直接从共享目录恢复，不会重复生成。
- 同 `job_id` 的 `request.json` 内容不同会被拒绝；POST 超时会用同一 `submission_id` 核对后再决定，绝不产生第二个 GPU 任务。
- 业务重启：运行中的任务保留并重新附着同一 `job_id`；若 Ray 重启后丢失旧提交则标记失败（interrupted），保留参数供人工重试，不会静默重跑；Ray 仍保留同一 submission 的终态任务时，重试会被拒绝并提示新建任务。
- 取消或超时会调用 Ray 的 stop 并等待终态；若 stop 已被接受但 Ray 仍在运行（例如容器忽略 SIGTERM），状态保持 `cancelling`、接口返回 pending 语义并继续轮询，不会把未终止的任务标成已取消；GPU 容器不重叠由执行器的 flock 与启动前回收保证，网页不持有设备。

### worker（GPU 容器内）

`python -m app.infer_job /job` 读取 `/job/request.json`，复用 `app/runtime.MingEngine` 与官方 Ming 适配层做推理，把 `result.wav`、`result.json` 原子写入任务目录；成功才 `exit 0`，失败 `exit 3` 并保留 partial。worker 显式测量并写入 `model_load_seconds`（含 checkpoint 加载，`checkpoint_load_seconds` 为官方加载耗时）与 `inference_seconds`（仅合成与写盘），供网页还原真实阶段耗时。读取请求时还会校验 `prompt_wav_path`：必须是 `/inputs`（可用 `MING_INPUTS_DIR` 覆盖）内的绝对路径、不能是符号链接、解析后不得越出挂载目录，否则以用法错误退出且不写结果。应用包装层输出任务标识、字数与耗时；Ming 上游仍可能记录完整生成提示词。执行器归档到 `worker.log`，这些日志按业务敏感数据保留在本机，不应复制到公网。

要求：`ming` profile 的 GPU 镜像需包含本应用代码（`/app/app` 可导入，或以 `/app` 为工作目录），`MING_REPO_DIR` 指向 Ming 源码，`MING_MODEL_PATH` / `MING_DEVICE` 与本地后端一致；镜像沿用现有 Ming GPU 基础镜像即可，不需要 Web 依赖或前端构建产物。

### 验证

`tests/test_ray_backend.py` 用进程内假 Ray 服务与假执行器覆盖：提交/轮询/阶段序列、同 id 重放不重复提交、请求冲突、运行中取消真实 stop、超时后禁止隐式重试、Ray 重启丢失提交、POST 超时按 submission 认领、瞬时断连容忍、只有 WAV 不算成功、执行器失败、`/inputs` 路径映射、CPU 探测、网页重启再附着，以及 ray 后端不导入 torch。

```bash
PYTHONPATH=. /tmp/ming-studio-venv/bin/pytest -q tests/
```

限制：真实 GPU 合成、Ray 集群、Docker 挂载和 flock 串行仍是部署侧验收项；本仓库测试不启动 Ray、Docker 或 Torch。

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
python3 services/ming-tts-web/bundle.py --output /tmp/ming-tts-studio-2.1.0.tar.gz
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
| POST | `/api/jobs/<id>/cancel` | 取消等待任务；Ray 后端运行中会停止 GPU 任务，本地后端返回 409 |
| GET | `/api/audio/<file>` | WAV 播放，支持 Range |
| GET | `/api/download/<file>` | 下载 WAV |

旧版同步 `/api/synthesize` 已由异步 `/api/jobs` 替代，外部脚本需改为提交后轮询任务。任务快照不会重新解析声音预设；删除或修改预设后，历史重试仍使用原参考音和参数。

## 数据边界

每次最多 2000 字、参考音最长 120 秒、单请求最多 30 MiB。会检查音频可解码、数值范围、非有限数、路径穿越和符号链接；不接受客户端提供的服务器路径。预设没有保存训练后的独立模型，而是保存参考音或音色描述及参数，实际合成仍使用 Ming 模型。

删除声音仅删除预设元数据，参考音保留给历史快照；删除生成记录会同时删除对应输出音频。没有自动按天清理音频，避免破坏收藏或历史记录。手动维护时应先备份并核对引用关系。旧版生成的 WAV 没有文本和参数元数据，因此不会伪造成新版历史记录；原文件仍保留。

2000 字是输入限制，不代表已经验证任意长稿的完整性。`max_length` 是模型按标点组织文本时的目标语义长度；模型会保留完整长分句，`raw_text` 开启后绕过此分段。`max_decode_steps` 是每段自回归步骤上限，过低可能截断；`sigma` 的额外噪声项在温度 0 时为 0。应用未另外实现长稿编排、多人协作、声线训练或录音转写。
