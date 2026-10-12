# 2026-10-11 普通网页运行身份

下面首轮内容为 G0 冻结观察，不代表修复后的当前容器。当前身份见文末 2026-10-12 记录。

本次观察入口 http://127.0.0.1:14188/。源码基底 HEAD 为
`d13951afbd4de4432d231a5921c8657daedab853`，分支 main；修复前工作区干净。
任务文件固定审查提交为 `2e19b756bbfbf7a898c01f7ca9a3e7650b65538a`，
两者差异仅为十个文档/属性文件；不能把当前工作树当作已经部署的 API。

| 对象 | 实际观察 |
| --- | --- |
| 前端 | Vite PID 47620，`node_modules/vite/bin/vite.js --host 127.0.0.1 --port 14188 --strictPort` |
| API | `raglocalfirst0930-api-1`，容器 `ec02b6450462`，端口 8000 |
| API 镜像 | `sha256:f16fe86da0ae05b6d4f3ad826aa9a7aec56331b05105e1aecf2aeab18d0cf7d8` |
| 解释器 | 容器 `/usr/local/bin/python` 3.11.16；源码验证 `.venv/Scripts/python.exe` 3.13.0 |
| 运行模块 | `/app/backend/app/main.py`、`/app/backend/app/bootstrap.py` |
| 启动 | `sh -c "python -m alembic -c alembic.ini upgrade head && uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"` |
| PostgreSQL | `raglocalfirst0930-db-1`，容器 `5c9a6eacb994`，宿主端口 25438，容器端口 5432 |
| DB 标识 | `postgresql+psycopg://<credentials-redacted>@db:5432/rag` |
| DB 卷 | `raglocalfirst0930_rag-db`；迁移 `0013_message_run_link` |
| storage | `/data/storage`，卷 `raglocalfirst0930_rag-storage` |
| Worker | 此次容器清单中没有运行 Worker；配置一致性 UNKNOWN |
| 当前 API 模型 | Ollama `qwen3.5:4b` / `bge-m3:latest`，不是本轮选定的云端配置 |
| 当前 Ollama | 宿主 11434 无监听；API 容器连接结果 111/refused，仅证明检查时不可用 |
| 开关 | 旧容器 cloud_enabled=true、Langfuse enabled=true、inline ingestion=false；无主动 Langfuse 请求 |

运行 bootstrap SHA256：`e782c53c6004fa6481c20326953637a4151a725e2bf61c3cb55a2c36aa3769d5`。

## 2026-10-12 恢复后的实际身份

入口仍为 `http://127.0.0.1:14188/`，Vite dev source，不冒称已构建发布版本。
HEAD 基底仍 `d13951afbd4de4432d231a5921c8657daedab853`，修复工作树；
`frontend-source-20261012.json` 保留五个实际前端源码 hash。
API/Worker 的运行模块字节与工作树 main/bootstrap/ingestion hash 已实际核对。

| 对象 | 当前实际身份 |
| --- | --- |
| API | `c074134d2d31165ce5c787d75326565dd665d39ab2df82972821953924776006` |
| Worker | `51ba0e8ebb343dc9409d78465272c46f6e884d25c07411a3e632963635398b1e` |
| API/Worker 镜像 | `sha256:4435b560d72c0f424074a461c9899e09dd9da3cfd6049489507ae3af25469774`，同一镜像 |
| DB | 原 `5c9a6eacb994...` 容器与原 `raglocalfirst0930_rag-db` 卷，宿主25438 |
| migration | `0023_service_token_policy`，实际迁移，不是模拟 |
| storage | 原 `/data/storage` 与 `raglocalfirst0930_rag-storage`；固定资源只读 `/opt/model-assets` |
| Redis | `b7ade590c3e03677698c781f68fa1669fa329551e258b02eb933a54672c8912e`，AOF，无宿主端口 |
| role 配置 revision | `bcd271c7-e820-45d4-9910-039384d268cd`；API/Worker 已加载，实际重启后保留 |
| 模型 | ordinary SiliconFlow BGE-M3、Xing cheap；direct DeepSeek flash expensive/vision；rerank 未启用 |
| 外发 | 仅目标 KB 得到本任务许可；新隐私测试 KB false；全局/角色门与月度预算仍实际执行 |
| Langfuse | 本任务普通容器 disabled；没有主动 Langfuse 请求 |

详细镜像、解释器、运行模块 hash、配置 readiness 和普通 ledger 的脱敏证据见
`var/reports/no-candidates-20261011/current-runtime-20261012.json`。
READY_CONFIG 仅配置检查，runtime_verified=false；不能当供应商当前可用证明。
DeepSeek 的一次真实生成调用出现 TLS 层错误；原 hello/你好仍未形成成功事实回答。
修复前工作树 bootstrap SHA256：`339745f28602cca41889968d2d28ec86d0d2c9cbb76c3be56bd27e894b103e61`。
前端是开发源文件服务，不宣称有已部署生产构建身份。

最新 Owner 选择：普通版 SiliconFlow `BAAI/bge-m3`，明确禁止 Pro；
cheap 使用 SiliconFlow Xing，expensive 和 vision 使用 DeepSeek flash。
Xing/BGE-M3 无次数限制；DeepSeek 新授权累计输入+输出上限 1,000,000 Token、无次数限制。
这是新的产品意图与授权记录，尚不是已装配的持续服务政策；不修改旧评测授权和 UNKNOWN 账本。
模型角色沿用 ModelRegistry/ProviderFactory；不把模型身份或容量数字写进 RAG 核心。

当前选择的 KB cloud_allowed=false。模型使用授权与该 KB 的资料外发许可分别记录。
没有自动改为 true；源码设置页提供明确的用户开关。没有业务库迁移、索引重建、服务重启或真实模型冒烟。

本记录是有限本机观察。代理路由为请求配置，实际上游身份 UNKNOWN；协作属于 MANUALLY_SUPERVISED_TRIAL。


续做观察：以上表格保留最初故障运行基线。负责人随后明确授权仅目标 KB 用于本任务云端处理；
实际 PATCH 后回读 cloud_allowed=true，其他KB未变，见 kb-egress-authorization.json。
新普通服务装配源码与固定 BGE/Xing/DeepSeek 容量资源已经准备，尚未替换上表旧 API/DB。
负责人指定 `.env.local` 供启动程序加载；隔离容器仅输出密钥存在性：SiliconFlow=true、DeepSeek=true。
这不代表密钥/网络/供应商额度已验证。未查看或输出密钥文件内容。
已保存实际业务 DB 备份，隔离恢复/0013至0023迁移及六进程额度检查通过；业务迁移仍 NOT RUN。
