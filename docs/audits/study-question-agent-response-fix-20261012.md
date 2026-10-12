# 2026-10-12 资料问答截断与 Agent 提示位置修复

## 1. 目标与改动

用户报告普通网页“我应该怎么学习”无法得到答复，错误又出现在用户气泡。只读回查原会话313909f3-34d9-4f26-8778-3d5bd27f2c60、原Run56f2e64b-fdda-4387-81c2-57a854b54ff4：原q0相同，原KB为d78f3434-0e8f-40bf-afcb-f1c2c311ff51，document_scope=[1fd5a928-d3d7-4bae-aa00-fc74701f61e5]。向量查询OK/4候选；Xing返回length，实际输出512 Token，终态MODEL_OUTPUT_TRUNCATED，并非检索不到资料。

- quick_chain / routes：仅普通服务启用输出预算对应的简洁回答提示，仍保留真实512输出预留；旧评测默认提示不变。
- bootstrap：复用已有truncated_escalation策略，仅普通服务启用；仍最多cheap一次、expensive一次，继续执行既有质量、权限、预算、取消、证据与最终提交检查。不是provider不可用后自动改换供应商。
- final_answer_commit：发现首次完整回复把未注明单位的数值列表补成“天”，增加被引用证据的时间数值列表/范围单位见证。裸数字不能获得新单位，也不能借未引用片段作证明。只做这一窄规则，不称通用语义验证或单位换算。
- cloud / model_usage：仅普通DeepSeek允许一次connect阶段SSL EOF重建连接。确认未发HTTP字节、保持原CA/hostname/TLS验证；每次连接及成功握手后重新检查许可、运行/租约与Token预留，使用剩余连接时间。证书失败、HTTP错误、读取/发送后错误不重发。固定诊断记录connection_setup_attempts，旧UNKNOWN不释放。
- ChatPanel / App / state：失败、澄清与表格提示从用户气泡移到Agent一侧的独立diagnostic条目。服务端关联仍在精确user/run，用户content不变；不插入已成功提交的助手事实、不提供引用、不加入有效短期记忆。刷新状态不再误称“请求状态无法确认”。截断提示明确说明输出上限。
- 新增原题/原范围真实网页脚本；修改对应预算、最终提交、TLS、装配与浏览器检查。未推进闲聊分流或其记忆方案，未更换配置模型、重建索引或引入依赖。

## 2. 执行命令

证据位于本机忽略目录var/reports/no-candidates-20261011，不提交原文、密钥或模型原始响应。

- `.venv/Scripts/python.exe -m pytest backend/tests/test_ordinary_deepseek_tls.py backend/tests/test_model_provider_contracts.py backend/tests/test_service_usage.py backend/tests/test_run_metrics.py backend/tests/test_final_answer_commit.py backend/tests/test_v6_output_cap.py backend/tests/test_p5_router_generation.py backend/tests/test_service_configuration.py backend/tests/test_answer_validation.py backend/tests/test_run_presentation.py -q -p no:cacheprovider`：0，229 passed / 5 warnings；study-complete-affected-20261012.txt；OFFLINE/SIMULATED。
- frontend工作目录：`npx tsc --noEmit`：0。初轮在根目录误执行npx tsc以及一次undefined类型检查失败不计通过；已在正确目录执行真实TypeScript检查。
- frontend工作目录：`$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'; npx playwright test tests/clarification-response.spec.ts tests/table-header-hint.spec.ts tests/sse.spec.ts --reporter=line`：最终0，47 passed；agent-diagnostic-browser-20261012-r3.txt；真实浏览器/SIMULATED API。前两轮旧NO_CANDIDATES文案与引用按钮文本断言不一致，失败保留。
- `docker build -f deploy/Dockerfile.api -t personal-rag:no-candidates-20261011 .` / `& ./scripts/deploy_no_candidates_service.ps1 -Deploy`：各0；api-image-build-study-connection-20261012.txt / deploy-study-connection-20261012.txt。
- `.venv/Scripts/python.exe scripts/replay_study_question_web.py --unit-guard --guarded-connection`：0；study-question-guarded-connection-real-20261012.json/.txt。真实普通网页、原会话/q0/KB/文档范围，1 POST；新Run d1eee158-93d3-4bac-b78a-5aab154d4221。cheap截断后expensive生成成功，4引用HTTP200且绑定同一活动版本，刷新同答复，原错误位于Agent一侧。
- `.venv/Scripts/python.exe scripts/capture_current_runtime.py --study-fix`：0；current-runtime-study-fix-20261012.json。API/Worker同镜像及实际源码hash一致，配置revision bcd271c7-e820-45d4-9910-039384d268cd，迁移0024，0模型调用。
- `.venv/Scripts/python.exe scripts/contract_test.py --report var/reports/no-candidates-20261011/contracts-study-answer-20261012.json`：1；全局31既有分层违规，normalized22文件/导入对与前序相同，0新增。OpenAPI路径/快照及固定SSE样本无漂移。整体仍FAIL。

## 3. 结果与失败证据

当前原题完整回复/4引用/原范围/Agent错误位置/刷新：PASS。末轮实际DeepSeek连接建立一次成功；两次连接的故障路径由模拟检查覆盖，不声称本次真实发生重连。最终回复保留复习间隔裸数值，并给出主动回忆、分散复习、动机与环境习惯等有资料出处的建议；局部人读核对不等于全面语义评测。

首次重放a2fb2792-0d31-4c3e-8aed-e559201b5a1f的协议/引用回读通过，但补了不受支持的时间单位，内容验收FAIL：study-question-first-semantic-audit-20261012.json。第二次21649a4f-ea51-4704-9709-7fc49e09ec3c为cheap截断、DeepSeek连接前EOF，业务FAIL：study-question-unit-guard-real-20261012.json。两个旧记录和其原始答复/预留不改写；末轮是新的明确请求，不伪造原Run成功。

## 4. 风险与遗留

外部网络和模型仍可能失败；仅已证明未发HTTP的EOF可做最多一次连接重建，已发送未知请求占用保持。正文HTTP请求不自动重试。模型实际账单和远端模型/本地token recipe等价性仍UNKNOWN。新单位规则只针对带引用的中文时间列表/范围，不能作为全部事实准确性的证明。历史旧回复保留，使用最新已验证回复；不把旧错误内容悄悄覆盖为正确。

闲聊分流和双短期记忆方案继续暂放。全量发布、全部数据集和所有历史答案重新审查NOT RUN，无延期批准、push或Tag。

## 5. 版本与下一步

本轮基底3d24d72c3f61056ceb93238f8840a3f4fd13c7f7，独立追加修复提交；正式版本以git log及提交后身份回执为准。下一步由负责人继续普通使用或提供已暂放的方案；不自动扩张功能。
