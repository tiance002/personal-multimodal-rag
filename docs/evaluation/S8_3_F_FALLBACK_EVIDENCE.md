# S8.3-F：SearchNothing Fallback 证据边界修复

## 结论

**本轮最小源码修复与离线合同验证完成，PASS（仅本Goal离线范围）。**

| 独立结论 | 结果 | 证据范围 |
|---|---|---|
| SOURCE_BOUNDARY_PASS | PASS | SQL不再读取Description，目录仅标题/类型；实际构造System/User请求无历史错误Description |
| FIXED_FALLBACK_PASS | PASS | 真实业务代码＋MockTransport／内存持久化＋脚本SQL合同；固定输出、空引用、SSE/历史/重放一致，无新增最终生成 |
| MODEL_FALLBACK_QUALITY | NOT_RUN | 无新真实模型回执，不能宣布幻觉已消除 |
| 真实PG/Redis/ECS集成 | NOT_RUN | 1项真实PG测试缺显式DSN而skip；本轮不连接ECS或更改运行服务 |

新增真实Provider **0**、输入/输出/总Token **0**、新UNKNOWN **0**。这是“没有真实发送”的0，不是把旧未知用量或Mock usage清零。没有重跑S8.3-R十二题、补内存、上传/索引、重建摘要或启用新授权。

## 1. 基线、分支与保护

初始HEAD为`86d42a7374f738c996e7804a067b0052291ec997`。实际分支当时已是`codex/ecs-deploy-skill-20261020`，未跟踪`.agents/`记录后保留；本轮创建`codex/s8-3-f-fallback-evidence-20261020`。执行期间并发提交`ea20815240f1a7ecd4c76c44dd1eb24b7fdf6ba5`加入部署技能及AGENTS增量，已读取其指令、保留全部9文件提交。本轮没有退回旧HEAD或把它重新纳入自身改动。

产品/测试实现提交：**`a5f85562b6aa11e4ec9a988c0c3fbde6fc856665`**。最终报告提交完整SHA在交付消息；也可执行`git log -1 --format=%H -- docs/evaluation/S8_3_F_FALLBACK_EVIDENCE.md`。

原主工作树16个既有未提交路径/20文件哈希仍匹配已保存基线。开始时两个未跟踪技能文件在并发提交中有合法增量，当前文件与该提交逐字节一致；不声称它们与初始哈希未变。对本轮范围外的service、coordinator、generation/context、development、检索主链及execution共10个文件，提交blob哈希与86d42a7一致。未连接/修改来源数据库、Gold、旧UNKNOWN或账本。

## 2. 根因：没有原文Context，仍有生成事实进入System

复用S8.3-R `engineering-unanswerable-postcode`两份真实回执：Search30条→Rerank低分→SearchNothing，未执行正常source_unit/Context。`ApplicationRuntime._fallback_documents()`原先执行：

```sql
SELECT title,file_name,file_type,description FROM knowledges ... LIMIT 50
```

然后将每份`description`截前100字符，拼入`kb_documents`。`RuntimeService.prepare_turn()`捕获SearchNothing后代入Fallback模板，构建`FinalizedContext(query,'',(),prompt,images)`；最终`prepare_generation()`把它放入System。因此“0个来源Chunk”不能说明“没有未经核实的正文描述”。

真实请求里出现了“2015年5月在瑞典皇家理工学院（KTH）举办……”的生成描述。默认模板当时已有“目录不能支持未读正文事实”的提醒，但两次真实答案仍将KTH及课件内容当成事实，说明仅加一句Prompt不足。

- A请求`fea2b6d6-52ce-4b1f-bf53-9c89dab566f4/answer/1/cheap`，文件SHA `01ab4da5c538f3a60b8d94f0010fcf4459d6698ed31f0784df57bed76cfd89f1`。
- B请求`5d337887-d49d-427c-b87c-8aacc20583eb/answer/1/cheap`，文件SHA `7a0500c4021ac5b959825a4bea49b2dae0bb6fd085877cc5e52542e9c8a6fbbb`。
- 两次各4条真实Provider调用（QU、Embedding、Rerank、Fallback生成），实际Token分别6285/6381；均为**历史复用**，不计本轮增量。

这与正常`bounded_source_v1`中Summary原文回取不同，后者未在本失败分支执行。

## 3. 最小改动与请求差异

| 文件／函数 | 改动 |
|---|---|
| `runtime.py::_fallback_documents()` | SQL只取title/file_name/file_type；格式化只访问标题、类型；空列表仍为空，目录仍最多50条 |
| `conversation_assets/fallback.yaml` | 只修改`model_fallback`和`default_fallback_prompt`，明确当前证据不足≠知识库客观没有信息；不根据文件名推断正文，不能引用未送达Chunk |
| `conversation_assets/manifest.json` | 同步Fallback资产真实SHA，保留完整性校验；YAML按已声明LF保存 |

目录改为：

```text
Document catalog metadata only (titles and file types; contents not read):
- {"title": "catvsdogdlpycon15se-150512122612-lva1-app6891_95", "file_type": "pdf"}
```

标题／类型用JSON转义，换行不能伪装成新的证据行；标题仍只是目录数据。没有读取KB简介或其他字段来替换Description，也没有改写任何已有Description。

tenant、KB、document_scope、enabled、deleted、session/principal检查与LIMIT50保持原SQL条件/参数。模型自定义Fallback Prompt仍按原合同保留；即使自定义模板使用`{{kb_documents}}`，也无法从该入口取得Description。恶意或模糊的自定义Prompt仍能诱导模型推断，这是未消除的质量风险，不伪称不可越过的语义校验。

使用保存的两条真实目录结构，经过当前`_fallback_documents → RuntimeService.prepare_turn → prepare_generation → ProviderModel.build_request_body`构建请求：原问题保留，模型Qwen3.5-4B、temperature0.7、max_tokens2048、thinking=false；System中不含原生成描述/KTH/Roelof Pieters，仅含明确目录元数据。System/User完整内容见[postfix-model-wire.json](evidence/s8-3-f-20261020/postfix-model-wire.json)。**这是离线重建，历史/运行夹具为合成，不是原请求逐字重放或新的生成成功回执。**

## 4. fixed/model合同与正常路径

沿用`GenerationCoordinator._fixed_fallback()`，没有另建兜底框架，也没有修改service/coordinator：

- 30个Search候选、Rerank合法结果全部0.1，按原0.3阈值/0.15保底触发SearchNothing；不把已拒绝候选当最终原文。
- fixed使用配置的简洁“当前知识库证据不足”文本，不声称知识库客观不存在信息，不拼场地、日期、机构、邮编或Chunk引用；SSE→提交→历史→重放文本一致。
- fixed没有新增最终生成；之前三次QU/Embedding/Rerank调用及各15个合成Token的注入Gate记录仍存在。`GenerationResult/FinalCommit.usage`只统计生成尝试，不能当整次问答Token；没有把整次问答写成Provider0。
- model仍可配置；内置模板尊重用户只依据知识库的要求，当前证据不足时说明缺口；在允许通用知识的场景，明确回答不来自知识库。它不是新的意图分类器，也没有关键词判断规则。
- 两种`source_evidence_mode`均覆盖；正常证据仍形成真实`chunk-0`引用。原有Rerank HTTP故障的Search降级保留有效证据；非法重复index和外发拒绝失败，不被伪装成空证据。合成UNKNOWN保持FAILED/UNKNOWN，已完成/UNKNOWN请求重放均不重新发送。

fixed文本本身是既有可配置字面量，本轮测试使用明确证据不足句；不能保证任意人为配置的固定文案都真实。模型回答中的新事实是否克制仍为NOT_RUN。

## 5. 实际RED/GREEN与命令

工作目录`E:/RAG-Worktrees/s8-six-dataset-evaluation`。**未实现的命令不得报告通过。**

```powershell
# RED，exit1：5 failed/4 passed（改生产代码之前）
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest -q --tb=short -p no:cacheprovider tests/weknora_port/test_fallback_catalog_boundary.py
# Catalog + P28，exit0：19 passed
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest -q --tb=short -p no:cacheprovider tests/weknora_port/test_fallback_catalog_boundary.py tests/weknora_port/test_p28_native_fallback.py
# fixed/model新增合同，exit0：14 passed（与最终联合结果重叠）
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest -q --tb=short -p no:cacheprovider tests/weknora_port/test_fallback_evidence_contract.py
```

最终联合命令（exit0，**237 passed/1 skipped in3.41s**；不将重叠运行累加成不同测试数）：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest -q --tb=short -p no:cacheprovider tests/weknora_port/test_fallback_catalog_boundary.py tests/weknora_port/test_fallback_evidence_contract.py tests/weknora_port/test_p28_native_fallback.py tests/weknora_port/test_fallback_output.py tests/weknora_port/test_p21_boundaries.py tests/weknora_port/test_m31_source_constraints.py tests/weknora_port/test_generation_storage.py tests/weknora_port/test_history_storage.py tests/weknora_port/test_turn_storage.py tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_http_api.py
```

PG写入、会话/历史读取、usage与幂等的**SQL合同使用脚本连接替身**；SSE使用内存Store，Provider使用MockTransport。唯一真实PG测试因缺显式隔离DSN跳过，不称为PG实测通过；没有创建数据库、启动Docker或读取密钥来补它。

保留的首次失败：YAML修改后资产SHA尚未同步导致完整性拒绝（现已同步，未关闭校验）；P28夹具仍使用已不存在的constructor参数，改为当前有界投影hook；两个历史断言仍要求旧的语言/引用文字，按已批准的当前实现校正，未改生成或引用生产代码；新夹具最初使用旧外发拒绝码，改为实际Gate拒绝码。最初sandbox测试启动无输出被停止，使用允许的本机进程后正常完成。全部失败日志保留，不把工具/夹具失败归成模型失败。

新增测试为`test_fallback_catalog_boundary.py`、`test_fallback_evidence_contract.py`及保存目录fixture；另外只修正上述三个既有测试文件的过期接线/断言。最终差异检查、staged资产SHA校验及原文件保护结果见机器记录。

## 6. 默认影响、风险与独立建议

默认仍是`fallback_strategy=model`、`source_evidence_mode=off`，没有自动切fixed。**预期行为变化只在将来加载本地新代码的模型Fallback目录和两份内置模板**：不再带Description；普通有证据RAG、Summary有界回取、Search/Rerank/Merge/RRF、模型与动态路由不变。ECS仍运行原代码，本轮没有部署、重启或改云开关。

独立建议：

1. **严格知识库问答建议由Owner选择既有fixed配置。** 现有`WEKNORA_PORT_FALLBACK_STRATEGY=fixed`可用，文案应只表达“本次检索证据不足”，不能断言资料客观不存在。本轮未替Owner更改部署默认。
2. **保留model Fallback供明确允许通用知识的场景使用。** 明确区分通用知识与已读原文，目录只用于列资料；自定义Prompt仍需Owner负责其边界。
3. **后续真实质量验证仍有必要，但本轮NOT_RUN。** 应覆盖来源限定的无答案、标题暗示机构/场地、目录列举及允许通用知识；核对完整事实和无效引用，不能只看没编造邮编。标题、自身知识或旧历史仍可能导致幻觉。

本Goal在离线验收完成后停止：不创建授权、不Push/合并、不部署ECS、不做S8.4或重新提交历史问题。报告和机器结果不能用作新云调用许可。
