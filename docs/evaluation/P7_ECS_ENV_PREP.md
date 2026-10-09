# P7 ECS Environment Preparation

**检查日期：** 2026-10-10（Asia/Shanghai）
**范围：** 只准备独立云端环境、存储/传输条件与数据源元信息；不实施 RAG 业务、不上传数据、不运行解析、索引或评测。
**状态：** `ENV_PREP_PARTIAL`
**状态含义：** 基础 Python、PostgreSQL/pgvector、隔离路径和后台主机能力已就绪；公开数据选定子集的身份/字节数仍未全部冻结，不能据此宣称 RAG 可部署或可评测。

## 1. 已准备环境和路径

ECS 项目预留根目录为 `/opt/rag-p7-r1`。它与 `/srv/rag-eval` 分开；本轮没有新建 systemd unit/timer、没有启动 Worker，也没有启用任何服务端口。

| 用途 | 路径 | 权限/状态 |
|---|---|---|
| 发行版预留 | `/opt/rag-p7-r1/releases` | `root:rag-eval`, `0750`；当前为空 |
| 上传暂存 | `/opt/rag-p7-r1/incoming` | `root:rag-eval`, `0750`；当前为空 |
| 数据根目录 | `/opt/rag-p7-r1/data` | `rag-eval:rag-eval`, `0750`；当前为空 |
| 运行日志预留 | `/opt/rag-p7-r1/logs` | `rag-eval:rag-eval`, `0750` |
| 任务状态/检查点预留 | `/opt/rag-p7-r1/state`, `/opt/rag-p7-r1/state/checkpoints` | `rag-eval:rag-eval`, `0750` |
| 临时文件 | `/opt/rag-p7-r1/tmp` | `rag-eval:rag-eval`, `0700` |
| 独立 Python 环境 | `/opt/rag-p7-r1/state/venv` | Python 3.12.3；仅含 venv 自带 `pip 24.0` |
| 无密钥配置模板 | `/opt/rag-p7-r1/config/runtime.env.example` | `root:rag-eval`, `0640`；外发开关默认关闭，不含 API Key |

上述目录由 `rag-eval` 服务用户可按用途访问；上传暂存目录只允许 root 写入。模板里的数据库地址指向新建的本机隔离库 `rag_p7_r1`，没有配置或输出任何生产密钥。没有为模板创建 systemd unit，也没有让已有服务读取它。

此前 R1 部署尝试在本轮范围替换前曾短暂上传业务代码和冻结集副本。范围替换后已清除 `/opt/rag-p7-r1/releases/r1`、`data/public`、`incoming` 中的包及校验脚本，并删除原项目虚拟环境；随后重新建立了上表所列的干净 venv 和空目录。当前 `/opt/rag-p7-r1` 只保留环境模板与空预留目录。已有 `/srv/rag-eval` 及其 SQLite 实验记录未改动。

## 2. PostgreSQL、pgvector 和 Python

- ECS 操作系统为 Ubuntu 24.04；系统 Python 为 3.12.3，隔离 venv 能导入标准库 `sqlite3`（SQLite 3.45.1）。`pip list --format=freeze` 只有 `pip==24.0`，没有安装项目依赖或 RAG 包。
- PostgreSQL 为 16.15，服务保持原状态。
- 安装了 Ubuntu 包 `postgresql-16-pgvector`（`0.6.0-1`）；新隔离库 `rag_p7_r1` 中确认 `vector:0.6.0` 和 `plpgsql:1.0`。该库约 7,822,359 B，owner 为专用 PostgreSQL 角色 `rag-eval`；角色可登录、非 superuser、无 createdb 权限。
- `rag_p7_r1` 只有 PostgreSQL 默认 `public` 非系统命名空间及扩展对象，没有业务表、迁移或索引。旧库 `study_platform` 未访问表内容、未修改。
- 没有对 `study_platform`、`/srv/rag-eval`、历史 SQLite registry 或旧评测记录做写入。

## 3. 已有服务及隔离

| 服务 | 实际状态 | 本轮操作 |
|---|---|---|
| `rag-eval.service` | `active`；ExecStart 为 `/usr/bin/python3 -m eval_center.server --host 127.0.0.1 --port 8787` | 只读确认；未重启、未部署覆盖 |
| PostgreSQL 16 | `active`，监听 `127.0.0.1:5432` | 保持运行；只在新隔离数据库安装扩展 |
| 新 RAG Worker / 评测任务 / timer | 不存在 | 未创建、未启动 |

当前观察到的应用和数据库端口仍绑定 loopback，没有新增公网监听端口。systemd 已能维持既有服务进程；未来任务可用独立 unit、日志、状态与检查点路径实现断线后后台运行，但恢复逻辑和任务服务尚未实现，当前不具备可验收的业务任务恢复能力。

## 4. 三套新增数据源与预计下载量

来源检查结果沿用已完成的 `DATASET_DOWNLOAD` 有界审计；未重试此前已超时的地址。本轮没有下载以下数据文件。

| 数据集 | 来源/版本状态 | 单文件及子集能力 | 许可与结论 |
|---|---|---|---|
| OmniDocBench v1.6 | [官方项目](https://github.com/opendatalab/OmniDocBench)当前已发布 v1.7，不能替换此前计划的 v1.6。项目 issue [#258](https://github.com/opendatalab/OmniDocBench/issues/258) 报告的 v1.6 Gold SHA-256 为 `a45cd84b04ad8b793e775089640e6b681209abea33ead54c1828ddca35fae496`；作者关联的 [HF v1.6 仓库](https://huggingface.co/datasets/MinerU25Pro-NIPS26/OmniDocBench-v1.6) 固定 revision `f14f2fa185c7af85c6b8149e11ec32be1ee0529f` 却给出 `OmniDocBench.json` 42,133,945 B、SHA-256 `0b7df8ac771a52af8c8ba02ea41a5580bd3861397c732f6a68fe25edb1c1e3019`。两者身份冲突。 | 24 页的图片 ID 尚未冻结，图片总字节数未知；HF Gold 无法证实与既有 Gold 相同。 | 项目数据声明 CC BY-NC 4.0（非商业研究）。**阻塞：** 不下载 HF Gold，不混用 v1.7 图片与 v1.6 Gold；先冻结匹配的 v1.6 图片/Gold 身份及 24 页清单。新增字节数 `UNKNOWN`。 |
| MMLongBench-Doc | 作者 [GitHub](https://github.com/mayubo2333/MMLongBench-Doc) 指向 [作者 HF 数据集](https://huggingface.co/datasets/yubo2333/MMLongBench-Doc)，固定 revision `2ff6aa9237fc777b6627dc57a486e9225ac5fb86`。单个 Gold/问题 parquet 为 82,073 B，SHA-256 `bcdac3c96669634c34184814cede4fe57cf7ac0f98dde0e85936394f6a56a02d`。 | PDF 按独立文件提供，支持只取指定 PDF。所需 3 份 PDF 和 8 个 QID 尚未冻结，3 份 PDF 大小未知；总量为 `82,073 B + 3 份 PDF`。 | CC BY-NC 4.0（非商业研究）。只在冻结 PDF/QID 清单后取必要文件；本轮不下载、不解析。 |
| HotpotQA | [官方站点](https://hotpotqa.github.io/)与[作者 GitHub](https://github.com/hotpotqa/hotpot)标记 CC BY-SA 4.0。官方 distractor-dev JSON 约 44 MB，先前请求超时，本轮不重试。作者账户 [HF 镜像](https://huggingface.co/datasets/hotpotqa/hotpot_qa) revision `d3686a5` 的完整验证集 parquet 为 27,452,575 B，SHA-256 `c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6`。 | 镜像是完整约 7,405 行 validation shard；未发现服务端 QID 子集下载能力。只选 8 题仍需先下载完整 shard，本轮选定数据量无法降到 8 条。 | CC BY-SA 4.0。**阻塞：** 不下载完整 shard；先确认官方地址恢复或批准读取完整镜像以本地筛选。精确选定子集字节数 `UNKNOWN`。 |

本轮使用的有界来源探测只读取三次 65,536 B Range 前缀，共 196,608 B 内存响应；没有完整下载、保存探测片段或处理数据。Range 前缀不是完整文件哈希。

## 5. 现有 479 条冻结集待上传白名单

本机原始来源为 `D:\RAG-Public-Bench\prepared\adapters\`。只上传下表九个文件；不包括原始语料、运行输出、历史索引/Embedding、Storage 对象、答案或报告。下列 SHA-256 为完整文件哈希，合计 23,243,808 B（约 22.2 MiB）；ECS 当前没有这些文件。

| 相对路径 | 大小（B） | SHA-256 |
|---|---:|---|
| `scifact/manifest.json` | 1,827 | `0f9957b10413f4ed85bac62a112e416485e749f940637cdb4cd393f3e5e0982c` |
| `scifact/corpus.jsonl` | 8,020,839 | `8608aa67e02a307745506d92316ebd0d1b2079226a576446c2bb5943e064f59f` |
| `scifact/cases/locked_holdout.jsonl` | 52,830 | `c66586f3d8d5ea9a21b82bcc998bbc606f2e11698d9bc3d2e8338a8e3f3f9479` |
| `miracl-zh/manifest.json` | 3,144 | `e9dbbac0001321a27d16d89d8851596481b078445efd0d0c3e34bd102450fe79` |
| `miracl-zh/corpus.jsonl` | 3,420,932 | `105dde269ea99ddee8ad0ebf8649631e88c3710be9ad2b54b4ced59e4a17ad29` |
| `miracl-zh/cases/locked_holdout.jsonl` | 23,945 | `2fd74aa0d6fcb8854828220f8d9424216a9b5d78ad441a6a9958f3198ab09c91` |
| `longbench-zh/manifest.json` | 1,795 | `891029790b066b02a134f469a5fda2298b3a42b28b2b9c05aa3cc735df02301e` |
| `longbench-zh/corpus.jsonl` | 11,656,046 | `d5f18bf83d652664d6778d1fab2eba0c1d6ef7781cc4c106e96217eca1cac0c1` |
| `longbench-zh/cases/locked_holdout.jsonl` | 62,450 | `2f48fb242653c2ce3f883796e822b685a65f3b4f0be9b59bab5da6896fb894c8` |

先上传到 `/opt/rag-p7-r1/incoming`，以 SHA-256 清单逐文件核验后再移入数据区；之后按数据集版本核 QID 数量、Gold 覆盖和文档 ID 映射。应继续排除历史 Ollama 向量，不与未来 SiliconFlow Embedding 混用。本轮没有执行上传。

## 6. 磁盘需求、资源与运行限制

- ECS 2 vCPU、内存 3,803,385,856 B（约 3.54 GiB），无 swap；检查时可用内存约 3.17 GB。
- 根文件系统 41,882,943,488 B，总可用 36,144,783,360 B（约 33.7 GiB）。没有独立数据盘/数据挂载的证据。
- 当前新增预留目录合计约 13.3 MB（主要是干净虚拟环境）。待上传冻结集 23,243,808 B；再加传输临时副本，按至少约 50 MB 预留源文件空间较稳妥。
- 三套新增数据的准确总量尚未知：OmniDocBench 24 页图片总字节数未知且 Gold 身份有冲突；MMLongBench 3 份 PDF 未锁定；HotpotQA 八题不能从已验证镜像直接按 QID 下载。索引/向量存储需求取决于未定的 Chunk 数和 Embedding 配置，故不能从下载字节推断索引容量。
- 当前剩余磁盘足以容纳已知约 23.3 MB 的基础文件和现有空环境，但尚不足以作出完整新增数据、索引构建和临时空间的最终容量承诺。未来解析、OCR、Embedding 和索引阶段应串行运行；在该规格上避免多个重任务并发。

## 7. 安全上传与断点续传

当前 Windows 有 OpenSSH `ssh.exe`、`scp.exe`、`sftp.exe`（OpenSSH_for_Windows 9.5p2）；已有 SSH 主机密钥校验通道可访问 ECS。Windows 未发现 `rsync.exe`，ECS 有 rsync 3.2.7，因此不能直接使用本机 rsync 续传。

可使用 SSH 加密的 SFTP `put -a` 尝试续传中断文件；续传成立的条件是远端部分文件与本地待上传文件拥有相同字节前缀。续传后仍须逐文件比对预先冻结的 SHA-256，校验不一致时丢弃部分件并重新传输。[OpenSSH SFTP 文档](https://manpages.debian.org/unstable/openssh-client/sftp.1.en.html)说明 `put -a` 会尝试恢复部分传输，并警告两端部分内容不一致会导致文件损坏。本轮没有演练中断/续传，也没有传输任何数据。

## 8. 实际执行命令与结果

| 命令/检查 | 结果 | 关键证据 |
|---|---|---|
| `df -B1 /`、`free -b`、`nproc`（在 ECS） | PASS，exit 0 | 本报告第 6 节资源值 |
| `/opt/rag-p7-r1/state/venv/bin/python -m pip --version` | PASS，exit 0 | `pip 24.0` |
| `/opt/rag-p7-r1/state/venv/bin/python -m pip list --format=freeze` | PASS，exit 0 | 唯一包为 `pip==24.0` |
| venv Python 导入 `sys, sqlite3` | PASS，exit 0 | Python 3.12.3，SQLite 3.45.1 |
| `psql ... SELECT extname || ':' || extversion FROM pg_extension ...`（仅新库） | PASS，exit 0 | `plpgsql:1.0`、`vector:0.6.0` |
| `systemctl is-active rag-eval.service` / `systemctl show ...` | PASS，exit 0 | `active`，原 ExecStart/loopback 8787 保持 |
| `command -v rsync; rsync --version`（ECS） | PASS，exit 0 | `/usr/bin/rsync`，3.2.7 |
| Windows `Get-Command ssh.exe,scp.exe,sftp.exe` | PASS，exit 0 | 三个 OpenSSH 客户端均存在；本机 `rsync.exe` 未找到 |
| RAG 业务测试、数据处理、索引、生成评测、Langfuse/DeepSeek 请求 | NOT RUN | 本轮禁止范围 |

先前曾尝试安装完整项目依赖作为 R1 部署的一部分；此后该 venv 已整体删除并重建，确认不保留业务依赖。过程中没有调用 DeepSeek、Langfuse 或其他生成/Embedding/Rerank 服务。该 R1 业务依赖安装不属于本次 ENV 验收证据。

## 9. 尚未解决的基础环境阻塞

1. OmniDocBench v1.6 的候选 Gold 文件身份不一致，24 页图片 ID/字节数也未冻结；禁止以 v1.7 替换。
2. MMLongBench-Doc 的 3 个 PDF 与 8 个 QID 尚未锁定，故完整下载量未知。
3. HotpotQA 8 个 QID 尚未锁定；作者镜像仅证明完整 shard 可按文件获取，不能按选定 QID 下载。
4. 新增数据、最终 Chunk/Embedding 配置及索引临时空间未确定，虽当前磁盘余量大，仍不能完成完整容量验收。
5. 后台 systemd 运行基础设施存在，但 RAG Worker、检查点格式和恢复/预算逻辑均未实现；它们属于业务阶段，本轮不实现。

**结论：** `ENV_PREP_PARTIAL`。环境预留完成不代表当前 RAG、Memory、ContextBuilder、Evaluation Center 适配或正式评测可以部署/验收。按本轮要求到此停止，等待业务能力完成后再启动下一阶段。
