> HISTORICAL RUNTIME RECORD: the development source default now uses docs/WEKNORA_DEVELOPMENT.md. This document retains prior service facts; no container replacement is implied.

# 当前普通服务运行说明

更新日期：2026-10-12。普通 API/Worker 已部署，公开小样本入库及真实资料在整库/单文档范围的问答、引用和网页刷新已验证。限定任务工程收尾见 [修复报告](audits/no-candidates-20261011/MINIMAL_FIX_REPORT.md) 最新节；不代表负责人发布验收。[暂停交接](audits/no-candidates-20261011/PAUSE_HANDOFF.md)保留历史记录。历史评测窗口不构成当前运行授权。

负责人暂放闲聊/资料问题分流及对应记忆方案，普通入口未启用该草稿。草稿与实验回执保留在本机忽略目录 `var/handoffs/input-routing-paused-20261012`。已执行的0024迁移和历史调用不删除；已有聊天实验记录不进入资料问答上下文。没有独立多轮闲聊记忆，不把草稿作为本任务交付。

网页“系统设置”提供 Embedding、Rerank、Chat cheap/expansive、VLM 的提供商、模型名称、HTTPS 接口网址和 API Key 配置。保存到本机共享存储 `private/model-settings.json`，不回传密钥。API 保存后切换配置，Worker 完成当前 Job 后加载；更换模型不覆盖旧索引和额度。2026-10-12已实际验证同一模型配置的保存、API/Worker同步及重启保留，0模型调用。未知模型可以保存，系统容量、协议和用量适配未完成时明确不可用；当前 Rerank 尚未启用。

## 模型与策略

模型由 `RAG_MODEL_REGISTRY_JSON` 和注册表的角色选择决定。默认角色为普通 SiliconFlow `BAAI/bge-m3`、cheap `XingChenAGI/Xing4.0-29B`、expensive/vision `deepseek-flash`。禁止使用 `Pro/BAAI/bge-m3`。容量适配器按实际 provider/model/协议装配；未支持的能力保持 NOT_READY，不自动换模型。

`deploy/service-policy.example.json` 记录本任务负责人提供的新普通服务授权。BGE/Xing 的零价预留依据负责人明确报价，不代表供应商账单核验。DeepSeek 的 expensive/vision 共享累计 100 万输入加输出 Token 的持久策略；预留和 UNKNOWN 均占用额度，重启不能重置。修改已用策略指纹不能扩大授权。旧 V6 评测政策与账本不变。

DeepSeek 采用 2026-10-11 核对的[官方峰时价格](https://api-docs.deepseek.com/quick_start/pricing/)，每百万输入/输出按 USD 0.30/1.20 保守预留；实际扣费保持 UNKNOWN。月度预算由 `RAG_MONTHLY_CLOUD_BUDGET_MICROUNITS` 控制，默认 0 会拒绝付费调用。旧费用记录不重定币种或改写数值。

## 本地容量资源

API 与 Worker 安装 `.[service]`，共享只读 `/opt/model-assets`。DeepSeek 固定官方 wheel 已核验 Linux x86_64；Windows 原生启动缺少该能力时保持 NOT_READY。

| 资源 | 固定身份 / 容器路径 |
|---|---|
| BGE-M3 | revision `5617a9f61b028005a4858fdac845db406aefb181`；`/opt/model-assets/bge-m3/tokenizer.json`；SHA-256 `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08` |
| Xing 五个文件 | `XingChen-AGI/Xing4.0-29B-A4B@baae3c3e813cad5f888f1f485cfff659c89076c5`；`/opt/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5`；逐文件哈希由现有 `siliconflow_xing_capacity.py` 核验，远程 Python 文件仅核验哈希，不执行 |
| DeepSeek tokenizer | 官方 recipe commit `8cadfede7063c896b944e7bae05daa3549ae97ea`；`/opt/model-assets/deepseek-v41/tokenizer.json`；SHA-256 `81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99` |

计数器核验本地完整协议，托管端等价性保持 UNKNOWN。发送前重新计数，供应商用量越过预留会持久暂停共享策略。VLM 只接收一张经过验证的 PNG/JPEG 原始字节，使用官方图像预处理和图像 Token 调整，拒绝外部 URL。生成描述保持 UNVERIFIED，不升级成原文事实。

## 公开启动配置

在 PowerShell 设置公开配置，密钥由负责人私下配置，不写入示例或报告：

```powershell
$env:RAG_SERVICE_MODEL_POLICY_JSON = (Get-Content -LiteralPath deploy/service-policy.example.json -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 8 -Compress)
$env:RAG_CLOUD_ENABLED = 'true'
$env:RAG_EMBEDDING_BACKEND = 'siliconflow'
$env:RAG_EMBEDDING_EGRESS_ENABLED = 'true'
$env:RAG_CHAT_EGRESS_ENABLED = 'true'
$env:RAG_RULE_ROUTER_ENABLED = 'true'
$env:RAG_PREFER_CLOUD = 'true'
$env:RAG_EMBEDDING_TOKENIZER_PATH = '/opt/model-assets/bge-m3/tokenizer.json'
$env:RAG_XING_CAPACITY_RESOURCES = '/opt/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5'
$env:RAG_DEEPSEEK_TOKENIZER_PATH = '/opt/model-assets/deepseek-v41/tokenizer.json'
```

还必须配置实际可用的 `RAG_REDIS_URL`（本 Compose 提供无宿主端口的 Redis，可设 `redis://redis:6379/0`）与已授权月度预算。VLM 用 `RAG_VISION_EGRESS_ENABLED=true` 独立选择；rerank 不在本任务免费报价授权中。全局/角色外发开关与知识库 `cloud_allowed` 都是必要授权，密钥存在性不能代替它们。

`docker compose -f deploy/compose.yml config --quiet --no-interpolate` 只验证结构，不证明可启动。不要保存含密钥的展开配置。启动前核对 Compose 项目、DB/storage 卷与备份；API 启动会执行迁移，未完成备份与恢复核验时不能直接替换旧业务容器。

## 状态与验收

`/healthz` 只证明存活。`/readyz` 和 `/api/v1/settings/models` 返回实际装配条件，标记 CONFIGURATION_ONLY / runtime_verified=false，不调用模型。静态 READY_CONFIG 不能证明网络、密钥、额度、索引兼容或 Scope 有候选。

实际运行基线和验收证据位于 `docs/audits/no-candidates-20261011/`。原问题/Scope已经回放：候选恢复，但最新hello运行在Xing答案校验升级后遇到DeepSeek TLS层错误；旧你好运行仍MODEL_UNAVAILABLE。不能据此宣称原问题成功或全面验收。实际公开小文档上传、问答、引用和刷新证据独立保留。旧身份不兼容时建立新不可变版本并验证后激活，不修改旧 identity 或删除过滤条件。
