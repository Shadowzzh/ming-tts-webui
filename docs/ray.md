# 可选 Ray 执行后端

默认部署直接使用本机 GPU，无需阅读本页。只有部署方需要将 Ming 接入共享 GPU 池时才启用 `MING_BACKEND=ray`；应用不安装 Ray Python SDK，也不自动创建或管理集群。

## 需要提供的基础设施

此适配器面向“Ray Jobs + 受控 GPU 容器执行器”，执行器在 Ray 环境中提供 `python -m gpu_runner --profile <profile> --job-id <id>`。普通 Ray 集群本身没有这个模块。部署方需要提供固定 profile、允许的模型镜像与挂载、GPU 串行与进程回收，并保证作业只在受控环境执行。

Ming 应用负责声音参数、输入路径映射和音频结果校验；通用执行器负责排队、启动、超时和回收。模型目录与大文件不通过 Ray 对象存储传输。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MING_BACKEND` | `local` | 显式设为 `ray` 才启用远程执行 |
| `RAY_JOBS_URL` | `http://ray-gpu-control:8265` | 部署方的 Ray Jobs HTTP 地址，建议显式填写 |
| `MING_RAY_PROFILE` | `ming` | 部署方登记的固定执行 profile |
| `GPU_DISPATCH_DIR` | `/app/dispatch` | 网页和执行器共享的任务目录 |
| `MING_INPUTS_DIR` | `/inputs` | GPU worker 看到的只读参考音路径前缀 |
| `MING_RAY_TIMEOUT_SECONDS` | `3600` | 单任务排队和执行的总等待上限 |
| `MING_RAY_POLL_SECONDS` | `1.0` | 状态轮询间隔，单次 HTTP 请求超时不超过 5 秒 |

部署方使用单独的 Compose，显式移除网页 GPU 设备、接入执行器网络并设置以上变量。网页保留上传、输出与状态目录；另外挂载共享 dispatch。不要将默认的独立 Compose 直接改成要求所有用户连接个人集群。

## 路径与 worker

例如，将同一宿主目录分别挂给：

| 数据 | 网页 | 执行器 / 模型容器 |
| --- | --- | --- |
| 参考音 | `/app/uploads` 可写 | 模型容器 `/inputs` 只读 |
| 任务根目录 | `/app/dispatch` 可写 | 执行器能看到同一目录并将单任务子目录挂为 `/job` |
| 模型 | 网页无需加载 | 模型容器 `/models` 只读 |

模型容器使用本应用的 `python -u -m app.infer_job /job`，工作目录为 `/app`。默认镜像的 ENTRYPOINT 是网页；执行器必须**覆盖 ENTRYPOINT**后运行 worker。模型进程以能读写任务目录的 UID 运行，参考音、权重保持只读。执行器应在下一个任务前确认前一个模型进程已经退出。

## 文件协议

单任务目录名为 32 位十六进制业务任务 ID：

| 文件 | 写入方 | 含义 |
| --- | --- | --- |
| `request.json` | Ming 网页 | 任务标识与 `parameters`，参考音映射到 worker 的 `/inputs` |
| `dispatcher.json` | 调度客户端 | submission、提交时间与客户端状态 |
| `executor.json` | GPU 执行器 | 容器的创建、启动、结束时间、状态及回收结果 |
| `result.json`、`result.wav` | Ming worker | 原子落盘的音频和业务结果 |

`submission_id = ming-<job_id>`。Ray 作业申请 1 张 GPU、0.1 CPU，entrypoint 为 `python -m gpu_runner --profile ming --job-id <job_id>`，其中 profile 按部署配置选择。

成功要求 Ray/执行器达到合法终态、模型容器退出并回收，且 `result.json` 为非空 JSON 对象。Ming 还会检查 WAV 存在且有效；只有一个音频文件不能证明执行成功。通用调度客户端的实现与状态格式见 `app/gpu_dispatch.py`，业务输入/输出格式见 `app/ray_engine.py` 和 `app/infer_job.py`。

同一 ID 的请求内容改变会被拒绝；提交响应丢失后按同一 submission 核对，不创建第二个 GPU 作业。产物完整时可以从共享目录恢复。失败后由用户创建新任务重试，不静默重新运行。

## 界面、取消和恢复

网页使用通用能力与任务状态，不通过后端名称决定能否取消。排队任务可取消；远程运行任务需要执行器支持停止、确认终态及回收后才能标记已取消，确认前保持“正在停止”。

调度阶段 `waiting_gpu`、`starting`、`running` 分别映射为排队、准备和生成。任务耗时中，只显示执行器或 worker 实际报告的排队、环境准备、模型加载、合成与总耗时；没有证据的字段保持缺失。

网页重启会重连同一任务 ID。若 Ray 重启丢失记录，则报告中断并保留参数供手动重试。更换后端前应先排空旧任务，不能把直接运行的模型进程当作远程执行器已经管理的作业。

## 验证

`tests/test_ray_backend.py` 和任务能力测试覆盖提交、轮询、同 ID 幂等、取消、断连、恢复、路径映射、结果校验和无 Torch 的网页分支。测试使用假 Ray 与假执行器。

真实部署还需验证共享挂载、GPU 串行、合成、运行中取消及模型进程回收。执行器升级和故障演练由部署仓库安排；应用仓库的接口测试不能代替这些验收。
