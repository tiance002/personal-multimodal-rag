# 标准 API / worker authority 接线：只读评估

读取 Owner 的 `E:\WK\go-linux\evidence\review-browser-4f3aedc2-20261015\authority-wiring.json` 并核对实际源码；本轮没有接线、持久化授权或启用发送。源码哈希见 `evidence/SUMMARY_READBACK_20261015/authority-readonly-sources.json`。

## 已有入口与最小复用方案

`entrypoint.create_app(settings=..., runtime_factory=...)` 与 `worker.serve(settings, once=..., runtime_factory=...)` 都接受服务端 factory；`development.assemble(settings, model_factory=..., permissions=..., costs=...)` 已把依赖传给 `ApplicationRuntime`。`runtime.LeasedSendGate` 每次调用现有 `P1SendGate`，保留精确 wire 身份、Scope/外发、预算占用、幂等及 UNKNOWN 处理。

最小方案是一个可信服务端 bootstrap 的 async contextmanager，共享同一套已批准权限/费用实现，委托 `assemble`，分别传给 API 与 worker。隔离验证时还可注入闭合 MockTransport 的 model_factory。无需重写运行时、发送门或 worker。标准 `entrypoint.main` 固定 Uvicorn factory 为 `entrypoint:create_app`，`worker.main` 仅从环境读取 settings；它们目前都没有选用该可信 bootstrap 的命令行入口。可以在后续获准实现时增加一个明确的服务端入口，避免任意环境 module import、客户端注入或重复两套装配。

这不是单个环境变量遗漏：端口内只有 `PermissionResolver`、`CostAuthority` Protocol，没有可供生产使用的具体实现或 authority 环境加载器。测试 `FixturePermissions/FixtureCosts` 不能成为生产授权。`/readyz` 仅检查两个依赖非 None，不证明授权有效、费用依据可靠或调用可发送，不能以占位对象使其变绿。

## 需要的配置及可信输入

既有必需环境项（统一前缀 `WEKNORA_PORT_`）：`DATABASE_URL`、`DATABASE_SYSTEM_ID`、`DATABASE_NAME`、`REDIS_URL`、`REDIS_RUN_ID`、`REDIS_PREFIX`、`STORAGE_ROOT`、`SECRET_KEY_FILE`。后者必须为已有/明确建立的 32 字节加密主密钥文件；配置不能打印 DSN、密码或密钥。可选 `TOKENIZER_ASSET`、`ROUTER_MODE`（默认 DYNAMIC）、`OLLAMA_URL`、`ORIGINS`。模型角色、endpoint、provider key 通过既有加密模型配置存储；不能把解密密钥与供应商凭据混为一谈，也不应增加另一套 provider key。

真实权限实现还需要：明确授权来源及有效期、global outbound 决定、服务器解析的 owner/KB 读取与 cloud_allowed、每次调用 provider/model/model_id/endpoint/capability/wire hash 精确绑定、完整来源清单和输入/输出硬上限。费用实现需要：account、UTC 月份、已批准月预算、带依据的 carry-in（UNKNOWN 不能视为 0）、价格版本、保守输入上界与预留估算、实际 usage 结算依据；继续使用既有 send_budget_months/send_calls，不重置历史或 UNKNOWN。

`PreparedCall` 只有 wire 哈希/字节数、身份、scope 和预算；`required_origin_kbs` 也不是内容哈希清单。一个只收到该对象的通用 resolver 不能从哈希逆推出 question/history/memory/evidence/image/system 的完整来源或可靠 token 上界。具体实现必须从可信组装路径获得并绑定真实来源/上界证据，或查询已存在且可信的服务器 manifest；不能对任意 call 填充“verified=true”。这是实现前必须解决的具体信息缺口。

## 是否新增持久权限或凭据

仅复用 factory seam 不要求新增数据库表、长期权限记录或供应商凭据。生产 resolver/costs 必须依托负责人批准且可审计的授权/费用事实；这些可以来自既有可信持久来源，不必新建 schema，但当前端口尚未提供该实现与来源接线，不能宣称现成可用。若拟新增长期授权文件/记录、凭据或批准真实外发，则是后续明确范围；本轮没有创建这些资源。既有初始化、加密密钥、模型配置与预算账本不能被“标准入口接线”隐式改写。

评估状态：`SOURCE_READ`；实现/标准 CLI 隔离启动验证 `NOT RUN`；真实发送 `NOT RUN`。保持默认 authority 缺失时拒绝发送、ready 503 的行为。
