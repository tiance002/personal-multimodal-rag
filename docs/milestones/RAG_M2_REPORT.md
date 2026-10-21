# RAG-M2 — 动态路由有效回答与模型配置可靠性

**M2_COMPLETE，2026-10-17（Asia/Shanghai）。** A 凭据恢复、B 有证据 Expensive、C 真实升级补救均完成。实际3次端到端问答、13次Provider调用，27,257输入 + 741输出 = **27,998 Token**；全部有usage，无新增未知调用。验收后停止。

## 版本与改动

基线 `80458e33a59f02fba5c476ddc97a916ede16874c`；固定 WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97`。配置修复提交 `77b4601`；隔离PG/诊断提交 `81db3dc953a0e085212b9526a387d22515c695e8`。最终报告提交由 `git log -1 --format=%H -- docs/milestones/RAG_M2_REPORT.md` 定位，交付消息给出完整SHA。

真实调用期间生产树一致：backend `9edb7a6c75d3daa6a1bd6074ee2d16947bb9035c`、frontend `1ddce3f4eb940c779f5bf7c7f171787c143110a8`。后续只补诊断、测试和报告。未重跑M1/P34/六套评测，未入库或改索引、Gold、全局路由、Rerank 0.3/0.15、Prompt或模型。

## A. 凭据恢复

根因：`PostgresModelRoleStore.update()` 先严格read全部旧凭据，任一坏密文会阻断所有角色保存；read_public又把解密失败显示成未配置。SecretCipher统一异常处理还把缺少cryptography包装成密文解密失败。

- `storage/configuration.py::_stored/update`：读取原始角色和密文；明确新凭据正常加密替换，空值逐字节保留原密文。保留事务、revision锁和Endpoint/Provider/Source重新绑定约束，无须解密被替换的坏旧值。
- `read_public/_public/read_available`：公开DTO的key_configured表示保存状态，另报credential_status=UNAVAILABLE及脱敏错误码；运行快照仅装配严格解密成功的角色。
- `model_registry.py::role_snapshot/RoleSnapshot.model_id`：坏角色不阻断好角色；明确请求坏角色仍拒绝。未把空值写回，也未把密文当API Key。
- `crypto.py::_aesgcm/decrypt`：依赖缺失报STORED_SECRET_RUNTIME_UNAVAILABLE；真错钥/坏密文仍严格报STORED_SECRET_DECRYPT_FAILED。enc:v1格式与旧明文兼容合同保持不变。
- `http_api.py::_public_model_configuration`、前端模型页及types：传递安全状态，显示“凭据不可用，可输入新密钥替换”。

离线真实ASGI配置接口可替换坏Expensive，其余四角色密文完全一致；真实隔离PG回滚事务中替换坏VLM成功，结束后夹具行不存在。没有在既有用户配置中故意写坏密文。正常严格解密、错钥、依赖缺失、revision冲突和重新绑定反例均保留。原旋转密钥测试的公开状态断言按Owner要求改为“已保存但不可用”，严格拒绝断言及Go加密Oracle未删改。

## B/C. 实际结果

均走正常FastAPI、租户332现有隔离KB、document_scope=[]、真实Embedding/pgvector/Hybrid/Rerank/Context和Provider；独立会话，每题只发送一次。

| 场景 | Search→Rerank→Context | 角色及结束 | 最终生成Token（入/出/合计） | 全链Token/调用 | POST延迟 |
|---|---|---|---|---|---|
| Expensive证据问答 | 30→1→1 | DYNAMIC 0.38→DeepSeek；stop，非Fallback | 999/425/1,424 | 9,633 / 4 | 59.130s |
| 第一次升级尝试：100汉字解释 | 30→1→1 | DYNAMIC 0.10→Cheap；stop，无升级 | 1,059/76/1,135 | 7,934 / 4 | 126.910s |
| 第二次：英文两句≤35词 | 30→2→2 | Cheap length→TRUNCATION→Expensive stop | Cheap 1,212/48/1,260；DeepSeek 1,121/34/1,155 | 10,431 / 5 | 222.848s |

模型固定SiliconFlow XingChenAGI/Xing4.0-29B、DeepSeek deepseek-flash、BGE-M3及bge-reranker-v2-m3。DeepSeek thinking disabled，既有正常凭据可用，无新增账户或密钥。

**B问题**：若电离层有效且卫星有效且无线电波属于电磁波且微波属于电磁波，是否可以根据课件区分两者的远距离通信方式？分别列出无线电波、微波的传播路径、介质、应用，并指出资料不足的部分。

Context及实际DeepSeek wire均含 `ionosphere refracts radiowaves` 和 `microwaves can be used to send signals to satellites`。完整答案正确说明两点，对未覆盖部分明确不足，finish=stop，wire max_tokens=1024，非Fallback。其资料完整性表述较保守；一次通过不代表复杂推理普遍正确。全文在results JSON。

**C第一次**：同一120 Token会话硬上限，Cheap用76 Token已stop，未触发升级。答案还加入“微波受大气干扰较小、通信更稳定”，当前Context未支持，保留为质量失败，不计作补救通过。

**C第二次**：独立问题要求英文两句、合计≤35词，新会话显式上限48。Cheap真实输出48 Token后length，草稿第二句末尾为 `... enabling long-distance communication beyond line-of-sight limitations`；系统按既有规则升级。Expensive同样max_tokens=48，实际34输出Token、stop，最终27词：

> The ionosphere is a charged layer of the upper atmosphere that reflects radio waves, letting them travel farther. Microwaves can be sent to satellites for long-distance communication.

额外Context含 `radiowaves are reflected ... off the ionosphere`，两句均有来源依据。两级原始响应有独立finish、usage及[DONE]；Prompt/Context SHA相同。最终role=expensive、upgraded=true、truncated=false、is_fallback=false；SSE答案=PG历史=已有Run重放，重放新增模型调用0。

没有修改输出预算合同。系统默认1024与会话显式120/48都经过既有summary参数路径；不根据值是否等于默认猜测用户意图，不在升级时抬高硬上限。同预算下更紧凑的Expensive完成了该次补救，但未证明默认1024下所有截断都能补救，也没有据此改变全局策略。

## 来源、用量与证据

KB `7f1d720d-9561-446f-a8cc-6a562c339d23`；document `8fdbe2af-9cc0-4b4a-a554-352a61ca5252`；source_version `3ff26984-be2a-4c7e-8b8f-bb8140c156f8`；原PDF SHA256 `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22`。核心Chunk `df6098cb-68e6-4546-b9e0-8682b4bf19e9` offset10575–11061；升级另保留 `3fc6cd69-fba9-46ef-bc8f-fa5b5e25fe3b` offset11825–12316。88个现有Chunk的文本、原件Hash和版本元数据前后一致。各级实际wire的文本messages与构造Context一致。

| Provider | 调用 | 输入Token | 输出Token | 总Token |
|---|---:|---:|---:|---:|
| SiliconFlow | 11 | 25,137 | 282 | 25,419 |
| DeepSeek | 2 | 2,120 | 459 | 2,579 |
| 合计 | 13 | 27,257 | 741 | 27,998 |

其中Embedding3次共138、Rerank3次共15,062、chat7次共12,798 Token。全部13行port_provider_calls为RESPONSE_RECEIVED并有usage；历史5张计量表Hash一致，未恢复ApprovedTokenAuthority或改写UNKNOWN。Attempt原字段cost_status=UNKNOWN不代表Token回执缺失；本轮不估算现金费用。

证据根 `E:/WK/run/rag-m2-20261017`：summary.json、snapshot.json、final-consistency.json、各query-*/result.json/messages.json/answer.sse、evidence-332/provider-requests、captures、stages。第二次升级有raw目录两份原始SSE。P17观察器complete=false表示在协议[DONE]后结束HTTP迭代、未读到EOF；实际两份字节都含[DONE]、finish与usage，可独立解析，结果并非未知。前两场只有P9解析回执及应用SSE，不声称保存了其原始Provider流。

## 测试与本地运行

以下命令实际执行，退出0，**116 passed（2.50s）**。日志targeted-tests.log；离线ASGI/存储double与其中1项真实PG回滚事务分别标注，均0云调用。

```powershell
$env:PYTHONPATH='E:\RAG-Worktrees\weknora-python-port-20261013;E:\WK\deps\reader;E:\RAG-Worktrees\weknora-python-port-20261013\var\weknora-port\runtime-deps'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "import os; from scripts.weknora_port_mvp_real import dsn; os.environ['WEKNORA_PORT_TEST_DSN']=dsn(); import pytest; raise SystemExit(pytest.main(['tests/weknora_port/test_crypto.py','tests/weknora_port/test_model_configuration.py','tests/weknora_port/test_model_credential_recovery.py','tests/weknora_port/test_model_store.py','tests/weknora_port/test_http_api.py','tests/weknora_port/test_generation.py','-q','-p','no:cacheprovider','--basetemp','E:/WK/run/rag-m2-20261017/pytest-final']))"
```

| 实际命令（python为上述解释器） | 退出码/结果 |
|---|---|
| `npm run build`，frontend | 0；tsc/Vite通过，274 modules |
| `python -B scripts/rag_m2_acceptance.py prepare` / `offline` | 各0；实例身份与来源检查，无云调用 |
| `python -B scripts/rag_m2_acceptance.py start` | 0；真实readyz正常 |
| `python -B scripts/rag_m2_acceptance.py query expensive` | 0；B通过 |
| `python -B scripts/rag_m2_acceptance.py query upgrade --cap 120` | 0；未触发升级，质量失败保留 |
| `python -B scripts/rag_m2_acceptance.py query upgrade-concise --cap 48` | 0；C通过 |
| `python -B scripts/rag_m2_acceptance.py summarize` | 0；wire、历史、Token对齐 |
| `python -B scripts/rag_m2_acceptance.py stop` | 最终0；只停止本轮API |

首次最终stop因诊断启动器改成M2但停止检查仍预期P31而安全拒绝，未杀进程；仅修正诊断工具预期脚本后停止成功。closeout.json确认56425/56426/56427无监听。原PG56423（systemID7696088403504697382）、Redis56424/DB15（runID dcff9d65a25725574ba0cfbc743d409fac211ef0）及其他容器保留。未重复浏览器、上传或OCR验收，无迁移、依赖安装或加密格式变更。

本机启动仍可用本脚本start，依赖既有隔离fixture、master.key和显式依赖目录；stop只停止其自有进程。M1启动器仍可用于React，按实际端口占用选择其一。query已有持久操作日志，禁止重发已运行操作。

## 遗留与停止

没有新引入的普通RAG主链阻断。已知质量局限：Cheap仍可能补充Context未支持的结论；SiliconFlow本轮Rewrite/生成延迟明显，最长整请求222.848s；小样本补救收益不能推广。建议M3由Owner决定是否先测量延迟与默认预算下的升级收益，或改善证据约束；本轮均未实施。

保留原.serena/project.yml、progress.md及既有6个未跟踪文件，不纳入提交。仅本地提交，不Push、部署、合并、打Tag或进入M3。
