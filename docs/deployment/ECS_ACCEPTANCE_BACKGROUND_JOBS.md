# ECS测评后台独立执行

## 使用方式

启动测评只需一次SSH连接。启动命令返回`start_acknowledged=true`与后台进程身份后，本机、SSH和Codex均可关闭。ECS负责运行任务、保存进度/日志/收据快照及最终结果；之后建立新连接查询和下载。

入口：`scripts/ecs_acceptance_job.py`，仅Python标准库，运行端要求Linux的`/proc`和`flock`。不增加服务、数据库表、工作流框架或模型调用路径。

这次已完成的ECS40原批次使用前台控制，原始记录保持原状。后台方式是后续测评的启动入口，不代表旧38题重新执行。

## 任务合同

先冻结任务目录、源码/Image/profile、payload SHA及有效任务grant。`prepare`复制不可变的`manifest.json`、`payload.py`和runner到`control/`；`start`仅启动一次。启动器不生成授权，不修改账本，也不绕过Registry、SendGate、LocalExecution或P5。

任务目录是`/opt/rag-s8/diagnostics/`下的新直接子目录。manifest的Python路径必须对应本次实测解释器；2026-10-22该主机为`/usr/bin/python3.12`，版本3.12.3。路径、实际源码及哈希每次按实况冻结。

manifest示意（非授权）：

```json
{
  "schema_version": 1,
  "kind": "acceptance",
  "job_id": "acceptance-unique-id",
  "goal": "本轮明确批准的任务",
  "source": "40位实际Git提交",
  "image": "sha256:64位实际镜像哈希",
  "profile": "64位实际profile哈希",
  "workspace": "/opt/rag-s8/diagnostics/已冻结任务源目录",
  "python": "/usr/bin/python3.12",
  "payload_sha256": "64位任务脚本哈希",
  "grant_path": "/opt/rag-s8/diagnostics/已冻结任务源目录/grant.json",
  "grant_sha256": "64位新有效任务授权文件哈希"
}
```

启动器不重复解析授权内容、检查账本或扫描数据库。`grant_path`与`grant_sha256`是可选的文件身份记录；提供时核对原字节哈希，不改变文件。授权、权限范围、用量与UNKNOWN保护继续由现有正式API、Registry、SendGate及LocalExecution执行。结果收集只读已有文件，不需要重开授权。

## 接入现有测评执行器

payload执行现有正式HTTP测评路径，原始执行账本继续保存在原合法路径。它通过以下环境变量得到任务输出目录：

- `ECS_ACCEPTANCE_JOB_ROOT`
- `ECS_ACCEPTANCE_OUTPUT_DIR`
- `ECS_ACCEPTANCE_MANIFEST`

payload应逐题原子保存`output/checkpoint.json`，stdout/stderr自动写入文件。任务结束时，在`finally`中把本轮选定的收据、SSE、原文回读和运行结果的**只读快照**保存到output；原始账本保持原位，不为后台执行重命名、重置或覆盖。

最终必须写入`output/result.json`，例如：

```json
{
  "runtime_complete": true,
  "unknown": 0,
  "unsettled": 0,
  "completed_submissions": 38,
  "quality_status": "REVIEW_REQUIRED"
}
```

`SUCCEEDED`只表示程序退出0且明确完成合同成立；答案正确率、证据支持和引用仍须单独评价。退出0但缺结果、结果不完整、UNKNOWN或未结算，均不能获得运行成功标签。失败不自动重试，任务进程也不自动重启。

## 启动、查询、下载

下列是命令模式，路径须替换成已冻结的真实目录；没有执行的命令不作为通过证据。

```bash
python3 /path/to/ecs_acceptance_job.py prepare --job-root /opt/rag-s8/diagnostics/acceptance-unique-id --manifest /path/to/manifest.json --payload /path/to/payload.py
python3 /path/to/ecs_acceptance_job.py start --job-root /opt/rag-s8/diagnostics/acceptance-unique-id
```

确认返回`start_acknowledged=true`、状态RUNNING与`supervisor`/`payload`身份后即可断开。`START_UNCONFIRMED`必须先读取状态，不得再次start。

以后通过新连接执行：

```bash
python3 /path/to/ecs_acceptance_job.py status --job-root /opt/rag-s8/diagnostics/acceptance-unique-id
python3 /path/to/ecs_acceptance_job.py fetch --job-root /opt/rag-s8/diagnostics/acceptance-unique-id --destination /opt/rag-s8/diagnostics/acceptance-export-unique-id
```

`fetch`只导出已有文件并记录逐文件哈希和复制期间稳定性，不会执行payload或发送模型请求。通过现有SCP/压缩归档传输导出目录到本机后核对`export-manifest.json`。正在运行的日志可能变化，`all_files_stable=false`时仅把该次导出作为进行中快照；任务结束后再获取稳定结果，不触发问答重试。

## 自动落盘与故障边界

- Supervisor和payload各自`start_new_session=True`，stdin为`/dev/null`，stdout/stderr接本机磁盘文件，关闭其他继承FD；不依赖SSH终端或本地Codex在线。
- 状态每秒原子替换并fsync，包含heartbeat、PID、boot_id和start_ticks。单次启动claim和payload claim不可复用；进程锁防并发重复执行。
- `output/artifacts.json`记录输出/控制文件哈希；不设任意文件大小、文件数量或归档体积上限。大文件采用流式复制/哈希。
- 不导出外部配置、环境变量快照、密钥/凭据文件或符号链接。任务自行选定的收据快照仅保存在output中。
- ECS重启、进程身份失效、发送结果未知时，状态为`INTERRUPTED_UNCONFIRMED`。原收据和UNKNOWN保留，后续只读对账；不自动重新执行模型调用。
- 这保证脱离**本机**运行，不承诺ECS断电后自动续跑。预算和外发保护继续由原有效任务合同执行。

## 测试环境简化原则

后续ECS测评保持五条边界：独立临时服务和目录；脱离本机运行；日志与结果持久化；记录必要的源码/镜像/会话/调用结果；状态不明的Provider调用不自动重试。

- 启动时核对一次实际源码与镜像身份；逐题不重复模块导入检查、整库指纹扫描或历史诊断容器枚举。结束时汇总必要状态。
- Docker资源参数使用实际主机支持的设置；不强加CPU调度参数，不为验证临时API重建整个Compose环境。
- 单份原始请求、响应和收据持续落盘；每题摘要记录q0、有效查询、Session/run/request ID、引用ID、终态和真实用量。完整内部状态仅在定位具体问题时开启，避免重复写parser_metadata和其他大对象。
- 不设未经实际数据支持的诊断文件体积限制；Windows读取使用短路径或扩展路径。复制和下载做哈希核对，工具读取问题与模型结果分开记录。
- 不新增授权或数值预算复核流程。现有Provider外发、权限、Scope及已批准用量限制继续在原执行链生效。

## 零调用验证

`scripts/ecs_acceptance_job_probe.py`只记录检查点、日志和最终JSON；不导入RAG应用、不连接PG/Redis、不发送网络请求。manifest须为`kind=zero_provider_probe`，结果须明确`provider_calls=0`。

本地定向测试和ECS断开启动连接后的真实进程验证将分开记录于同目录的`ECS_ACCEPTANCE_BACKGROUND_JOBS.results.json`；本地mock不作为真实SSH断开证明，零调用探针不作为新一轮问答验收。
