# P21：关闭公开引用与知识库检索边界

日期：2026-10-17。状态：P21_COMPLETE，完成 Owner 限定的两批定向验收；另有一项基线旧元数据回归失败保留。不是 Owner 里程碑批准。

基线 `8e45001737c4d0e99ff5a0c5b72ee0a16136d2b0`；实现提交 `2c08fc6c494e2bab80eefe53709823fa35719e8d`。实际测试文件 SHA256 见同名 results.json 和 `E:\WK\run\p21-20261017/tested-source-manifest.json`。本轮使用本地离线及正常 HTTP＋真实 PG/Redis＋模型传输替身验证；真实云调用 0、实际云 Token 0。

## 实际修改

| 文件／函数 | 行为 |
|---|---|
| `generation/types.py::GenerationRequest` | `citations_enabled` 默认 false；既有显式开启路径保留。 |
| `references/registry.py::expand_text`、`decoder.py::CitationStreamDecoder` | 清理裸、闭合、分块及 EOF 残留内部标签；代码字面量、图片、链接、资源恢复与开启引用时的来源校验保留。 |
| `frontend/src/weknora_port/WeKnoraPythonApp.tsx::MessageContent` | 普通问答不渲染引用按钮／切块抽屉；新 SSE 和旧历史在显示时清理标签；旧消息不做 SQL 改写。 |
| `retrieval/query.py::needs_retrieval/resolve_intent/understand_query` | 未知标签默认检索；原问题明确要求知识库／获取已有来源时优先检索；追问需真实历史，图片／文档分析需实际附件。合法社交 intent 保留，不引入第二分类器。 |
| `retrieval/pipeline.py::run` | 使用上述最终 intent；需要检索时清除不应沿用的闲聊系统覆盖。 |
| `service.py::RuntimeService.prepare_turn` | 复用 SearchNothing 和固定 fallback 收口；有 KB 目标且最终无有效证据时，不调用答案模型或文档列表自由回答。 |

固定回复：`当前知识库中未检索到足以回答此问题的有效证据。请补充相关资料，或调整问题后重试。`

内部检索 Context、真实原文、来源版本、权限检查与 Token 门仍走原路径；`runtime.py` 和生成协调器字节未变。没有迁移、依赖、模型、路由评分、Rerank 阈值或 MinerU 修改；不接入 P20 审计。

## UTC 历史复核

P19 原记录显示 Search/Rerank/Context 均为空，`fixed_fallback=null`，最终用户材料为空。结合原有原文投影在检索启用且证据为空时抛 SearchNothing 的保护，可确认当时走了不检索分支，不能归因为引用 Decoder 丢失。

历史未保存 Rewrite 响应正文，**具体 intent 值仍未证实**。本轮确认并修复了未知 intent 可授权不检索的代码缺陷，以及缺少历史／附件时仍可能旁路的边界。原 UTC 文本在空库、模拟未知 intent 下通过正常 HTTP 验证：执行检索后固定证据不足回复，答案模型调用 0。该合成响应不冒充历史真实输出。

## 实际命令及结果

共同 Python 环境：`E:/RAG quention/.venv/Scripts/python.exe -B`；`PYTHONPATH` 为项目根、`E:/WK/deps/reader` 和项目 `var/weknora-port/runtime-deps`。

- `python -B -m pytest tests/weknora_port/test_p21_boundaries.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_service.py tests/weknora_port/test_generation.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_references.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_siliconflow_rerank_usage.py -q -p no:cacheprovider --basetemp E:/WK/run/p21-20261017/offline-final-v2`：**273 passed，退出 0**；日志 `offline-final-v2.log`。
- `python -B E:/WK/run/p21-20261017/run_pg_v2.py`：**4 passed，退出 0**；实际 pytest argv、工作目录和退出码在 `pg-final-v2/command.json`，日志 `pg-final-v2/pytest.log`。使用真实 loopback FastAPI、PG/pgvector、Redis，模型响应仅在 HTTPX 传输边界模拟。
- `npm run build`：**退出 0**，`frontend-build.log`。
- `npx playwright test tests/weknora-port/adapter.spec.ts`：最终扩展测试的整套运行 **7 passed／1 个测试断言误匹配失败，退出 1**；保留 `frontend-playwright-edge-final2.log`。此前整套 8 项也有通过记录。
- 修正该断言后：`npx playwright test tests/weknora-port/adapter.spec.ts --grep "partial SSE reference tags"`：**1 passed，退出 0**，`frontend-playwright-partial-edge-final.log`。其他七项未改动；最终断言修正后没有再次整体重跑。8 个不同场景均取得当前有效通过证据。
- 浏览器为现有 Edge：`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`；最终 TEMP/TMP、profile/cache 使用 `E:/WK/run/p21-20261017/frontend-tmp`。这些是 React＋API mock 测试，不冒充浏览器到 PG 的全链路。
- `python -B E:/WK/run/p21-20261017/integrity_audit_v2.py`：**退出 0**。705 条 SiliconFlow 原记录逐字段等于 P20 快照，644 settled／61 UNKNOWN；同月预算、DeepSeek 原调用和账本相同；18 个历史报告／Gold 相关文件与基线一致。

正常 HTTP 覆盖：错误 chitchat 分类的明确 KB 请求仍检索；真实 Context 进入生成请求；默认无公开引用／标签；原文版本和位置可回读；历史和相同 request 重放不重发；正常社交／历史追问可用；空库 UTC 固定拒答；跨 Scope、外发、预算拒绝；处理中取消与 UNKNOWN 保留。没有新增真实模型质量评分。

## 保留的非阻断失败

补跑 `test_reference_metadata_pg.py` 时，显式开启内部引用后在 `ref['matched_content']` 处 KeyError；已得到合法引用和原文字符位置，但该可选检索匹配字段缺失。

使用 `baseline_metadata_v3.py` 从固定基线加载六个后端模块，得到**同一断言失败，退出 1**（`baseline-metadata-v3.log`）。加载器只额外提供一个未使用的测试 helper 导入常量，未更改基线执行行为。原文投影和引用输出协调器本轮未改；这是基线已有契约不一致，本轮原断言保留、无推测性修复。建议由 Owner 后续安排该字段契约核对。

首轮旧默认断言失败、测试 harness 属性错误、错误文件名命令（退出 4）、基线加载器前两次导入错误及前端测试误匹配日志均保留，未改写成通过。**未实现的命令不得报告通过。** 全项目回归与真实云模型测试 NOT RUN。

## 范围、资源与收尾

PG：127.0.0.1:56423／weknora_python_port_20261013，system identifier 7696088403504697382。Redis：127.0.0.1:56424 DB15，当前 run_id `baf0e65bbd8c7cc4a53a61ae90a333d9075e142b`。各集成测试使用新租户、UUID Redis prefix 和独立 E 盘资产目录；没有 flush 或旧资料重建。

本次正常启动已安装 Docker Desktop 和原 EviRoute PG/Redis；两容器原先停止，验证后已恢复停止状态，卷保留。HTTP/Vite/fixture worker 由测试收尾，无相关监听残留；其他容器未操作。Docker Desktop 保持供其他现有容器使用。

本轮无真实模型调用、无需新围栏；历史 UNKNOWN 未释放、未重结算。初始 `.serena/project.yml` 和六个未跟踪文件不纳入提交。全部原始证据在 `E:\WK\run\p21-20261017`。完成即停止，不 Push、部署、合并或 Tag。

限制：明确来源语句保护覆盖冻结的中英文表达，不能证明任意自然语言分类都正确；非空 Context 也不等于语义支持。代码示例中的字面 `<ref>` 是正常代码内容，按要求保留。
