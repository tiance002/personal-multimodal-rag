# S7 GraphRAG 定向质量验收

## 结论

**COMPLETE_WITH_UNRESOLVED**。工程合同修复通过；真实答案质量仍为 **PARTIAL / KEEP_EXPERIMENTAL**。Graph 可作为经 Owner 另行批准的 S8 **独立受控实验臂**，不具备默认推广依据。本轮到此停止。

- Graph 基线 `855e2d32bf85f50fbb9fa12ace9b0a51e1adbf67`。
- 独立工作树 `E:/RAG-Worktrees/s7-graph-targeted`；分支 `codex/s7-graph-targeted-20261019`。
- 实现提交 `eaf639e7c3395b47419ab84e06632fd4fe5dfae8`。本报告所在完整提交可用 `git log -1 --format=%H -- docs/evaluation/S7_GRAPH_TARGETED_QUALITY.md` 核对，最终交付另列完整 SHA。
- Top1 仍在 `E:/RAG-Worktrees/rerank-top1-experiment`，基线 `d00c272526e3d22890099099520ad777b07aba03`。没有合并、Cherry-pick、Push、部署或进入 S8。
- 原主工作树 HEAD 未变，执行前保护的 **18 个文件逐字 SHA 未变**；历史 Gold、来源、索引、角色和 UNKNOWN 均保留。日常 Graph 默认仍关闭。

机器结果：[S7_GRAPH_TARGETED_QUALITY.results.json](S7_GRAPH_TARGETED_QUALITY.results.json)。
真实证据：[s7-graph-targeted-20261019.zip](evidence/s7-graph-targeted-20261019.zip)，SHA-256 `ca7850ba2907f181773f6516d7711c0997ba13da808bd0ec9f5df2beb91b992d`。186 个显式选择的输出经秘密模式扫描和 ZIP 完整性检查，含有序候选、实际请求/回执、原文、版本、阶段轨迹、SSE、历史及包内 SHA 清单；不含 local.json、密钥、Bearer、环境和服务日志。

## 1. 已确认缺陷与最小修复

| 问题 | 修复 | 保护与证据 |
|---|---|---|
| `\w` 边界把连续中文、中英混排误拒绝 | 新增 `graph/script_boundaries.py` 精确原文 span；抽取 `_occurrences` 与查询 `mentioned` 共用脚本边界 | 不插空格、不改原文/offset；英文单词、设备编号、数字、连接符、组合字符继续保护。中文子串命中不等于实体消歧 |
| Summary/Caption 复制原文被误判来源已覆盖 | `graph/retrieval.py::_coverage/fuse_paths` 要求同 KB/document/current version、真实 raw 类型、完整正文和 ID/subchunk/parent 映射 | Summary/Caption 不能借复制文字/ID 获得原文身份；真 Parent/合并原文可去重。trace 明确 added/covered/budget_dropped |
| 同实体不同关系被稳定 ID 主导，目标在截断前丢失 | 复用已有 tokenizer、停用词、Jaccard；去除共享 seed 文词后，关系谓词和原始 quote 的查询重合度参与排序 | 相关性位于每节点出边12、路径3的截断之前，ID 仅同分排序；不改 hop2、最多4原文块/新增4096字符/总14000字符的既有预算 |
| 新工作树正常 API 在模板身份检查失败 | `conversation_assets/manifest.json` 仅更正系统模板 SHA | `4aee9f17e05f740c27dfa9ba4916f1c81c4fa90e` 的模板 Git blob、当前 HEAD 和工作文件实际 SHA 都为 `1a97e9d2586c84a494c0f64a340b80b55dad94416cc13546d17aede619ac34fa`，旧清单为 `6a24...`；无 CRLF、Prompt 正文未改。正式模板读取修前 exit1、修后 PASS |
| 执行工具把 SSE 观察结束当作正常完成 | `scripts/s7_graph_api_check.py` 联合 SSE 与持久化 assistant 判定 | 错误、空正文、缺完成事件分别失败；首次历史文件原样保留，没有事后改成 PASS |

正式仓库回归验证修复后期望，而非把附件审查脚本的旧缺陷断言当通过。旧实现的边界/检索定向用例暴露了失败；其中一个时间 qualifier 测试夹具本身不符合既有合同，另行修正为原文中的合法时间短语，未把夹具错误计为产品缺陷。最终受影响集合 283 项通过。

生产改动仅 Graph 的两个模块、新边界 helper、清单一字段；其余 `scripts/s7_graph_*.py` 是本阶段显式诊断/执行工具及对应定向测试。没有更改 Rerank0.3、Query/生成 Prompt、角色、DYNAMIC、原 Chunk 或数据库 Schema。

## 2. 冻结范围、来源与对照合同

可信 tenant332，subject SHA `00f2fd3dad2b741924376adab7fd29c12a93bc7a1fe4e8e1f0f97cacd418fa83`；Hotpot KB `96a9eebd-ffb2-4bc6-9bea-3ed015845479`。

- 每题同一 **24 文档、59 块、35 原文块**的完整 Search 语料池，包含干扰；document_scope 为空，不为每题删到两篇 Gold。
- 本阶段仅对提前冻结的 **12 文档/19 原文块、6,619字符**真实建图：120 节点、28 边、19 个持久化贡献。被拒绝的格式/来源关系保留，不重抽选优。
- 每份文件 SHA、版本、原始 span 均核对；19块的抽取输入只有真实原文，没有 Gold。
- 4组固定对照直接重用 M4 保存的原 Query、完整有序 Search/BGE 请求与回执；原生 Rerank/MMR 结果先与历史逐项匹配，再同池 Merge/TopK，只改变 Graph off/on。旧分数未用于新 Passage。
- 新 Qwen 生成固定模型、系统 Prompt、历史空、语言、渲染时间、temperature0.3、thinking=false、max_tokens1024。Books 两臂 wire SHA 相同，输出差异只能说明模型波动。
- Normal API 使用正式 `assemble(principal=verified332)` / `entrypoint.create_app`、独立 loopback 端口、短期 Bearer、独立 Redis 前缀，无 Worker。日常 tenant1 配置不变。
- tenant332 数据库默认 Cheap 仍为 Xing；隔离服务器内存明确绑定该身份已登记 Qwen 候选，QU 同样显式绑定合法 tenant332 ID，不继承日常 tenant1 ID。原生 snapshot、DYNAMIC、DeepSeek 和 durable Gate 保留，数据库角色无写入。

## 3. 八个场景结果

| 场景 | 模式与必要来源 | 实际结论 |
|---|---|---|
| Franklin EN | 新图离线 1/2 | 只有 Franklin 单跳；`Leyden jar` 与真实锚点 `A Leyden jar` 无可信别名映射，未自动桥接。新生成 NOT_RUN |
| Franklin 中英混排 | 新图离线1/2；正常 API 发送前失败 | 字符定位已通过，但首次请求遇模板清单错误，SSE/PG error、Provider0。没有重发，不算模型质量失败 |
| Frost | 固定 off1/2 → on1/2 | 新增同作者诗集块，缺 seminar/location 第二跳；两臂未给出 Franconia，on 诚实不足。PARTIAL |
| Romeo | 固定 off1/2 → on2/2 | 两臂原文均已有 Zeffirelli 事实；off 核心正确但扩展名未证，on 错答 Giuseppe Tomasi di Lampedusa。**on生成 FAIL** |
| Books（留出） | off/on2/2，wire 完全相同 | Gold 标题对，但把 first published 说成完成时间；on 的1962仅在文件 SHA 字符串出现，非原文事实。两臂 PARTIAL，非 Graph 收益 |
| Tribometer（留出） | off/on2/2 | 两条真实发明事实支撑正确回答，两臂 PASS；Graph 多加 jar 单位块，属于无益噪声 |
| Leyden 单跳 | 离线1/1、3路径、stores在前 | 只有离线路径观察，未新增生成，不写真实 QA PASS |
| Maintenance 无答案 | 正常 API Search30→Rerank0→Graph补1原文块 | 资料无维护间隔；Qwen 明确说明不足，**限定反例 PASS**。非官方无答案 Gold |

四组固定生成按完整答案事实支持审查：**2 PASS、5 PARTIAL、1 FAIL，分母8个臂**；短 Gold 核心正确5/8，不能替代完整支持性。官方支撑文档覆盖合计6/8→7/8，也不能被描述为必要事实或答案准确率的相同增益。

| 固定场景 | off Context字符/块 | on Context字符/块 | 解释 |
|---|---:|---:|---|
| Frost | 2632 /2 | 2943 /3 | +311字符，没有地点事实 |
| Romeo | 2226 /3 | 2839 /4 | +613字符，补第二来源但最终生成错误 |
| Books | 1745 /3 | 1745 /3 | 无改变，输出差异为生成波动 |
| Tribometer | 5264 /7 | 5847 /8 | +583字符，无益新增原文 |

## 4. 正常 API、wire 与历史

两次提交均有独立 UUID36、会话和执行账本。第一次 Provider0 失败，不用第二次请求替换其结果。

第二次 maintenance：实际 Rewrite `Franklin bells maintenance required days`；Search30、BGE保留0、普通TopK0，Graph选择2个单跳路径、回取同一真实原文块500字符。原文 chunk `0f650d87-44cc-4fe5-8a91-30844d2578e0`、版本 `d89f6a4e-2db0-4378-9cac-6160ffd12253`，完整正文确实进入 Qwen wire。正常发送 QU→Embedding→BGE→Qwen，共4次；无升级、无 model fallback。

回答明确“资料中未提及维护周期或时间间隔”，没有编造天数；Sources 中的真实文件名可回读。finish_reason=stop；SSE、PostgreSQL assistant 均 completed；两次 GET 的消息数据一致，回读新增调用0。全程9.040秒，不是单次模型时延。每调用的发送/响应时序保存在 evidence 中；被动流观察的 `complete=false` 仅表示未见传输EOF，未伪报其为完整raw捕获；可信usage、stop、SSE和落库分别验证。

正常 API 的 Graph-off 固定补验：**NOT_REPLAYABLE / NOT RUN**。第一次无准备轨迹；第二次普通TopK为空，Graph-off会进入未执行/未捕获的原生 model fallback。没有自行拼空Context或近似fallback，也没有补发模型。固定生成实际8次，而非许可上限10次。

## 5. 用量、授权与数据保护

Owner 本轮新许可已记录，旧 S6 授权未复活。每份执行文件≤12次/100,000Token，统一父上界60次/500,000Token；串行预留、实际usage结算。末尾所有文件 CLOSED，未用17次/444,997Token授权空间撤回。

| 本轮新增 | Provider次数 | 输入Token | 输出Token | 合计Token |
|---|---:|---:|---:|---:|
| Graph建图 DeepSeek | 19 | 11,044 | 7,928 | 18,972 |
| Graph固定生成 Qwen | 8 | 10,189 | 1,254 | 11,443 |
| Graph正常API辅助＋生成 | 4 | 6,273 | 133 | 6,406 |
| **Graph小计** | **31** | **27,506** | **9,315** | **36,821** |
| Top1独立分支 | 12 | 17,446 | 736 | 18,182 |
| **共同许可实际总计** | **43** | **44,952** | **10,051** | **55,003** |

新 UNKNOWN0；这不表示历史UNKNOWN为0。只读核对 PG Provider总记录661→704，历史UNKNOWN **8→8**，未重发、释放或修改。冻结的24篇文档、59块、版本、向量身份/维度、KB许可、默认模型角色前后完全一致。Top1临时批准KB许可另由正式API恢复false。

## 6. 实际命令与退出码

Python：`E:/RAG quention/.venv/Scripts/python.exe`；工作目录为本 Graph worktree；PYTHONPATH使用已存在reader/runtime-deps，PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_graph_script_boundaries.py tests/weknora_port/test_graph_s7_retrieval.py tests/weknora_port/test_graph_extraction.py tests/weknora_port/test_graph_quote_completion.py tests/weknora_port/test_graph_retrieval.py tests/weknora_port/test_graph_service.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_evidence_capture.py tests/weknora_port/test_s7_graph_offline_check.py tests/weknora_port/test_s7_graph_contrasts.py tests/weknora_port/test_s7_graph_generation.py tests/weknora_port/test_s7_graph_api_check.py tests/weknora_port/test_s7_graph_api_off.py
# exit0: 283 passed in3.87s
python scripts/s7_graph_offline_check.py --output E:/WK/run/s7-20261019/graph-offline.json
# exit0 OFFLINE_PASS，复用历史回执，不是新增真实发送
python E:/WK/run/s7-20261019/build_graph_once.py
# exit1: 19次发送已结算，最后只读汇总误用get_graph_batches；独立load_graph回查exit0，无重抽
python E:/WK/run/s7-20261019/run_fixed_graph_once.py
# exit0，8次新固定生成
python E:/WK/run/s7-20261019/run_graph_api_once.py franklin-mixed
# 旧执行工具exit0，但实际SSE/PG为FAILED、0Provider；修后分类器此类返回exit2，原回执未改
python E:/WK/run/s7-20261019/run_graph_api_once.py franklin-maintenance-unanswerable
# exit0，正常API完成；人工来源审查PASS诚实不足
python scripts/s7_graph_api_off.py --output E:/WK/run/s7-20261019/graph-api-off-contrasts.json
# exit1 NOT_REPLAYABLE，Provider0
python E:/WK/run/s7-20261019/verify_graph_sources.py
python E:/WK/run/s7-20261019/close_stage.py
# 均exit0，来源/角色保护与授权收口
python E:/WK/run/s7-20261019/write_graph_delivery.py
# exit0，秘密模式扫描与ZIP test通过
git diff --check
# exit0
```

工具研发过程的失败亦保留：初次测试文件尚未落盘的命令exit1；C盘tmp_path不符合既有执行文件D/E盘保护导致1个fixture失败，修fixture后通过，生产守卫未放宽；未计作模型质量失败。没有运行全仓/六套数据集或旧75次阈值实验。

## 7. 风险、决策及下一步

- **来源/边界工程修复：PASS**。定向多分支反例在既有12/3截断下，由0/5匹配目标关系改善到5/5；这只是可复现离线合同，不是5个真实问答。
- **路径语义：受限**。同义动词零词面重合、反向入边、时间约束未实现语义推理；稳定ID不再主导，但Jaccard不是事实判定器。
- **图抽取/锚点：未解决质量限制**。新Franklin抽取的字面锚点不一致、Frost缺桥接。没有手填alias/Gold来造跨文路径。
- **来源增加与噪声：真实观察**。Romeo来源更完整但答案更差；Tribometer多一个无益块。不能宣传“Graph已改善多跳准确率”。
- **生成事实可靠性：保留独立问题**。正确Zeffirelli原文送达仍可答错导演；Books可扩展无依据年份。这不是本轮再改S5 Prompt/模型的授权。
- **正常API正向事实闭环：本轮未完成**，第一次模板阻断、没有重发；第二次诚实不足闭环通过。S6旧成功只作历史证据，没有记成新成绩。

建议下一步由Owner决定是否进入S8：仅将Graph作为独立可关闭实验臂，保留同池干扰、全部必要事实和完整答案人工审查；不推广默认，也不叠加Top1冒充单变量收益。Top1的S8资格与风险见它自己分支的独立报告。
