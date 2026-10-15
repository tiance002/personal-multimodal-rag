# 更正记录：模型 ID 来源核对 + 四项完成度表述修正（2026-10-15）

Owner 指令：不得把测试夹具中的 Qwen 当作正式 VLM 选择；核对 DeepSeek 型号是否为 `deepseek-flash`；更正四项“完成”表述，明确真实问答与评测仍未完成。本文件为**更正与只读核对记录**，不新增授权、不发起真实请求。

## 1. 模型 ID：各来源逐一核对（含来源与效力等级）

| # | 来源类型 | 位置（可核查） | 角色 → provider / model ID | 效力 |
|---|---|---|---|---|
| 1 | **隔离数据库**（合成测试数据，非生产库） | PG `127.0.0.1:56423/weknora_python_port_20261013`，表 `port_model_configuration`（`tenant_id=1`, `revision=18`） | embedding→siliconflow `BAAI/bge-m3`；rerank→siliconflow `BAAI/bge-reranker-v2-m3`；cheap→siliconflow `XingChenAGI/Xing4.0-29B`；expensive→deepseek `deepseek-flash`；**vlm→siliconflow `Qwen/Qwen2.5-VL-72B-Instruct`** | 测试数据（`base_url=https://fixture.invalid/v1`；`api_key` 为 `enc:v1:` 加密块，未回显明文）。**vlm 行是测试值，不是正式方案** |
| 2 | 代码默认（路由角色） | `backend/weknora_port/generation/router.py:6` | cheap→siliconflow `XingChenAGI/Xing4.0-29B`；expensive→deepseek `deepseek-flash` | 正式代码常量 |
| 3 | 代码目录/注册表 | `backend/weknora_port/models/catalog_resources/models.generated.json` L1421-L1435 | `deepseek-flash` = "DeepSeek V4.1 Flash"（provider `deepseek`） | 正式目录 |
| 4 | 配置示例（模型注册） | `deploy/clean-slate/models.json` | chat_cheap→siliconflow `XingChenAGI/Xing4.0-29B`；chat_expensive→deepseek `deepseek-flash`；embedding→siliconflow `BAAI/bge-m3`；rerank→siliconflow `BAAI/bge-reranker-v2-m3`；**vision→deepseek `deepseek-flash`** | 正式示例 |
| 5 | 配置示例（Owner 授权策略） | `deploy/service-policy.example.json`：`owner-20261011-bge-m3-normal-v1`；`owner-20261011-xing-normal-v1`；`owner-20261011-deepseek-normal-v1` | deepseek `deepseek-flash`，`roles=[chat_expensive, vision]`，`token_limit=1000000` | Owner 授权参考（示例） |
| 6 | Owner 文档（注册边界） | `docs/model-provider-registry.md` L7-L11 | 五角色表：vision = deepseek `deepseek-flash`；cheap/expensive/embedding/rerank 同上 | 项目文档 |
| 7 | Owner 文档（历史 Go 服务，历史记录） | `docs/RUNNING.md` L13、L26 | 默认 embedding `BAAI/bge-m3`、cheap `Xing4.0-29B`、expensive+vision `deepseek-flash`；tokenizer 资产路径 `…/deepseek-v41/tokenizer.json` | 历史运行记录（非当前授权） |
| 8 | Owner 交接（受保护，本轮未改） | `docs/weknora-port/OWNER_DEEPSEEK_AUTHORITY_HANDOFF_20261015.md` L43 | “DeepSeek UTC 月限 100 万 Token 明确……” | 受保护交接事实 |
| 9 | **测试夹具**（合成装配，非正式） | `tests/weknora_port/test_authority_cli_pg.py` `ROLES` | vlm→**siliconflow `Qwen/Qwen3-VL-32B-Instruct`**；其余同 1 | 夹具专用，**不得作为正式选择** |
| 10 | 合成传输 | `backend/weknora_port/models/synthetic.py` | 不绑定任何真实 model；对任意 `model` 返回固定 canned 响应 | 封闭夹具 |

**结论：正式 VLM 角色是 `deepseek` / `deepseek-flash`（与 expensive 同模型、同月额），不是 Qwen。** 先前任务三报告中的 `siliconflow Qwen/Qwen3-VL-32B-Instruct` 行来自第 9 类测试夹具，已更正。

## 2. DeepSeek 型号核实：是否为 `deepseek-flash`

**是。** 依据来源 4/5/6/7：provider `deepseek`，精确 model id `deepseek-flash`；目录名称为 "DeepSeek V4.1 Flash"（`models.generated.json` L1422），且 catalog 中 `deepseek-v4-flash` 被标注为“retired name, served by V4.1 Flash”（L1439）——即 `deepseek-flash` 是当前有效 ID。Owner 策略将 `chat_expensive` 与 `vision` 合并在同一 `deepseek-flash` 账户，月限 **1,000,000 Token**（cumulative input+output）。

命名分歧如实上报：`docs/RUNNING.md` 使用 tokenizer 资产目录 `deepseek-v41`；`models.generated.json` 另有 `deepseek-v4-pro`（V4 Pro，非本项目所选）与退休别名 `deepseek-v4-flash`/`deepseek-chat`/`deepseek-reasoner`。本项目 **未** 使用 `-pro`。除资产目录用 `v41` 而目录名写 `V4.1` 外，未发现冲突的 model id。

## 3. 四项完成度表述更正（原表述过于笼统，此处纠正）

**总述更正：四项任务目前只在“隔离 / 合成 / 模拟传输”层达到各自可验证状态；真实问答闭环与真实语料评测仍未完成（NOT RUN / BLOCKED）。**

| 任务 | 提交 | 实际达到的状态 | 明确的未完成项 |
|---|---|---|---|
| 一 | `4fa522b7` | 共享 authority 装配在**合成 + closed MockTransport + 隔离 PG/Redis** 层 PASS；默认拒绝、ready503 保持 | `approved` 计量未实现（`APPROVED_METER_UNIMPLEMENTED`）；真实来源/计量/carry-in 未核实；**真实请求 NOT RUN** |
| 二 | `ee5919f` | 五项主链检查在**真实 CLI 进程 + 隔离 PG/Redis + mock 模型**层 PASS | **真实浏览器 NOT RUN**；authority 模式下的追问/隔离/升级 wire 级断言为复用证据；**真实模型 NOT RUN** |
| 三 | `e22e428` | 层 1 真实 Office 转换 PASS；层 2 真实**本地** Ollama PASS；Gold 模式离线校验 PASS | **层 3 云端真实模型、层 4 真实最小问答闭环、层 5 三模式对照、层 6 分 Gold 指标：NOT RUN / BLOCKED**（无授权 + approved 计量未实现） |
| 四 | `e31e249` | 重打包 + 隔离安装 + installed 验证 PASS；文档/学习地图/冗余清单更新（零删除） | 均为隔离/合成验证；**不含真实运行、真实模型或生产可用性认定** |

先前“任务三……完成”的写法**不准确**：层 4–6 从未执行，也未获得云端授权；本文件据此更正。

## 4. 尚缺的生产接线与计量（据 `AUTHORITY_IMPLEMENTATION_DESIGN_20261015.md` §2/§9、`AUTHORITY_BOOTSTRAP_ASSESSMENT_20261015.md`）

1. **approved 计量 recipe**：`measure_final_input` / `TokenReservationBound` 的可靠输入/输出上界（含 embedding/rerank/vision 各自语义），缺失则该能力拒绝发送。
2. **逐来源真实 SourceFacts**：question/history/evidence/memory/document/image/system-template 的原始与派生双 hash 绑定；当前 `bind_synthetic` 的 whole-wire slice 不能充当真实来源证明。
3. **carry-in / 旧 UNKNOWN 可靠上界**：必须从既有 Token 账本核实，不得填 0、不得从金额反推；同账户其他消费覆盖未知时真实启用 BLOCKED。
4. **approved 账户/provider 月度映射**：`TokenMonthlyAuthority` 的 account/provider/UTC month 真实映射与 `limit_tokens`（DeepSeek=1,000,000）；其他 provider 额度未明确不得默认无限。
5. **approved 配置解析 + readyz**：`load_authority_settings` 当前对 `mode=approved` 直接抛错；`/readyz` 的 approved 合同校验与 `--authority-config` 的 approved 分支。
6. **稳定 operation_id**：`RuntimeMemoryModels.complete/embed_query` 与 maintenance 的无 operation_id 分支必须收窄到持久任务/消息/版本派生 ID。

**状态更新（2026-10-15，提交 `docs/weknora-port/AUTHORITY_APPROVED_IMPLEMENTATION_REVIEW_20261015.md` 同批）**：第 1/2/4/5 项已在契约层实现（approved 配置解析 + `ApprovedTokenAuthority` + `measure_final_input`/`bind_wire_facts` + approved 装配接线 + `/readyz` 识别），全部 fail-closed 且零真实请求。**仍缺**：应用侧按能力组装真实 `CallContext.source_facts`（未接前标准 API 的真实问答在 `bind_wire_facts` 处拒绝）、从既有账本实际核对 carry-in/UNKNOWN 上界、真实凭据与额度。

## 5. 边界

- 本记录不改受保护文件（`OWNER_DEEPSEEK_*`、`AUTHORITY_STOP_STATE_*`、`P6_ORDINARY_GO_BUILD_*`）。
- 未新增任何真实授权、密钥或外发；未调用真实供应商。
- 上述第 4 节为**待实现**清单；若获准，将走隔离/闭环 TDD 实现，保持 fail-closed 与 synthetic 模式既有断言不放松。
