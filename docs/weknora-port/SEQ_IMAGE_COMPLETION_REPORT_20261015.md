# seq 与图片父块补全限定报告 — 2026-10-15

## 1. 目标与改动

基线 `3b528f22d12d2c50fa19c195f79dd1681879c0d0`；固定 Tencent/WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97`。本次仅关闭两个已确证缺口，Office `e1b945b9` 与 Proxy `7197e3e1` 已被父级独立审查接受，不重开其范围。

- `storage/knowledge.py::_read_chunks`：DTO 同时写入 `seq=row["chunk_index"]`，包括0和非零。上游 `knowledgebase_search_results.go::buildSearchResult` 的 Seq/ChunkIndex 同源。AST 去掉新增 seq keyword 后与基线完全一致，SQL/tenant/KB/document/启用状态防线未改。
- `storage/hybrid.py::PostgresKnowledgeRepository.hybrid_search`：hydrate 后、返回前复用现有 `retrieval/merge.py::collect_image_info` 两层批量 collector。对应上游 `processSearchResults → EnrichSearchResultsImageInfo → collectImageInfoByChunkIDs`，包括 skip_context_enrichment=True。不改候选截断、分数、match_type、matched_content、排序。
- `retrieval/merge.py`：新父聚合仅投影 url/original_url/start_pos/end_pos/caption/ocr_text 六个 Go ImageInfo 字段，后到同 key 只以非空 OCR/caption 覆盖；不带入 page/layout/status/binding 等 Owner 扩展。初始非空 image_info（包括 []、空白、坏 JSON）保持字面原值，已有 child 原始字符串不改写。
- 两个产品调用点传入真实父 source rows；enabled text/OCR/caption 在两层均核对父 KB/document/source_version。tenant 仍由 SQL和服务端 principal 验证；跨域、权限、取消、deadline 继续抛出，普通查询失败允许不补。legacy 两侧均无 source_version 仍可用。summary 不参与图片 collector，不改变其模型生成/父绑定/引用语义。
- 测试：新增 `test_search_image_completion.py`、`test_search_image_completion_pg.py`、Go fixture 与 `scripts/weknora_port_image_completion_oracle.py`。既有 `test_hybrid_storage.py` 的假驱动现正确将无子块的 parent 查询返回空，不能再把全部 source rows 冒充 child rows。没有新依赖、配置开关或模型路由变化。

配置优先级：不新增配置；源行为图片信息补全不受 skip_context_enrichment 禁用。既有全局/KB/model/router设置顺序、0.35阈值及升级开关均未改变。生产调用都携带父 source 版本锚点。

## 2. 执行命令与证据

所有相对路径以独立工作树 `E:/RAG-Worktrees/weknora-python-port-20261013` 为 cwd；解释器 `E:/RAG quention/.venv/Scripts/python.exe -B`。Python/Go缓存、临时目录仅 E。精确 argv/环境目录、stdout/stderr、exitcode 和 JUnit 全在 [evidence/SEQ_IMAGE_COMPLETION_20261015](evidence/SEQ_IMAGE_COMPLETION_20261015)。未实现的命令不得报告通过。

| 执行 | 退出码 | 实际观察 |
| --- | --- | --- |
| `-B evidence/.../run-check.py red tests/weknora_port/test_search_image_completion.py` | 1 | 12失败/11通过，未改运行代码；seq、缺少补全和扩展字段反例 |
| `-B evidence/.../run-check.py summary-guard-red tests/weknora_port/test_search_image_completion.py::test_summary_child_without_source_version_is_ignored_by_image_collector` | 1 | 实际PG发现新增guard过宽；忽略类型summary没有source_version，不应阻断图片收集 |
| `-B scripts/weknora_port_image_completion_oracle.py` | 0 | 实际Go1.26执行16组提取原函数体；仅类型包名/typed fixture repository 适配，不是完整上游服务 |
| `-B evidence/.../run-check.py offline-final tests/weknora_port/test_search_image_completion.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_hybrid_storage.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_reference_metadata.py` | 0 | 124通过，含真实Go同fixture完整Python搜索对照、两层/多父/disabled/字面非空优先/坏JSON/类型/Scope/tenant/KB/doc/version/缺源行与取消边界 |
| `-B evidence/.../run-check.py pg-dto-fixed --pg-env tests/weknora_port/test_search_image_completion_pg.py` | 0 | 1通过；实际ASGI HTTP upload/chat/source/history路由+真实PG/Redis+真实图片worker，所有模型MockTransport。seq0/7原文、两父六字段、4child原串、源版本拒绝、Context注入、引用HTTP200/JSONB/历史相等 |
| `-B evidence/.../run-frozen-search.py` | 0 | 原先实际Go完整搜索冻结语料，当前真实PostgresKnowledgeRepository完整搜索10结果；18共同字段含seq全部相等、image_info相等、4child原串相等；12索引行/10source行保持原样。连接read-only，0模型尝试/写/DDL |

完整命令名以对应 `*.command.json` 为准，表中 evidence/... 仅用于缩短显示。124项与16Go证据在恢复后按源码/fixture SHA绑定复用，没有无理由重跑。`source-binding.json` 将固定上游Git blob与本机文件、移植文件、fixtures、入口与最终证据逐项SHA绑定；融合、P5、Scope、Office/Proxy相关AST与基线相同；四个既有untracked文件哈希匹配。证据目录的局部 .gitattributes 保留原始执行字节（避免 core.autocrlf 改写）；重复的 runtime.patch 以原始artifact保存，实际源码仍以正常diff审阅。

原始PG尝试均保留：pg-initial对HTTP异步pending错误作同步断言；pg-worker误以为第二次claim必须成功（HTTP pump可能已领取）；pg-pump揭示真实过宽summary guard，独立反例后修复；pg-guard-fixed把c1误当目标原文，实际可为summary；pg-context-handle在最后HTTP回读误用content，真实DTO字段是text。最后仅改测试读取text，保留原文相等断言，未改产品DTO或排名。模拟回答根据实际Context中index7原文handle引用，不写死c1。

基础设施恢复：Docker engine缺失管道、之后两测试容器停止均先只读确认；pytest未在这些前置失败后启动。Owner打开Docker后，仅核验并启动原批准的独立PG56423/Redis56424（见isolated-start.json），没有替换镜像、改配置、删除卷或启动业务容器。首次诊断从旧cwd误导import而失败，改为独立cwd只读诊断退出0，证据pg-pump.domain-diagnostic.json。历史失败、UNKNOWN和所有原始证据保留。

## 3. 结果

本次两个命名修复：SOURCE_READ / IMPLEMENTED / OFFLINE_PASS / ISOLATED_INTEGRATION_PASS。真实ASGI路由在进程内执行，PG/Redis为真实TCP；不声称浏览器或真实HTTP监听服务器验收。Go16组是提取原函数体typed fixture adapter；冻结10结果对照复用原实际Go完整搜索输出，不重新声称完整Go应用重跑。

原始反例闭环：两个父块6字段一致，三条summary非零seq恢复为1，4个OCR/caption child字面信息不变；候选集合与原Go一致。融合tie顺序记为 `UNDEFINED_FUSION_TIE_ORDER`，全局ParadeDB segment统计已变化，未重申分数或tie顺序等价，不另造BM25/排序策略。

REAL_PROVIDER_PASS / CUTOVER_PASS / 整体Goal验收：NOT RUN / 未声明。Office验收口径仍是正式应用upload/read方法入库（其报告不代表HTTP路由），外部转换替身且DOC oracle共享DOCX桥接；Proxy真实代理未跑、两项原拒绝待Owner决定。本次新ASGI验证不追溯扩大Office/Proxy的证据范围。

## 4. 风险与遗留

- 本轮不增加真实供应商调用；mock输出/向量仅验证代码、传递与持久化，不证明模型质量、OCR质量或实际费用归集。
- 新增父版本检查是必要安全封装；summary被忽略，未向其添加伪造原文版本。已有非空child扩展字段仅原串保留，不上提到新父聚合。
- 上游URL聚合map顺序和fusion tie顺序不承诺；比较字段/集合，不自行排序改变业务结果。
- 既有独立测试库的新tenant/docs历史保留，无旧业务数据库/配置/密钥/卷改动，无UNKNOWN释放、历史删除、push/tag/deploy。没有自行批准延期或里程碑通过。

## 5. 版本与下一步

本地实现提交SHA由最终Git结果提供；回滚以该独立提交为单位。父级下一步独立只读审查本提交及限定证据。整体Goal仍依既定阶段和真实门授权推进，不重复已接受Office/Proxy，不触发真实模型或生产切换。

暂存检查：完整 `git diff --cached --check` 退出2，仅原始失败日志的6条pytest E-line尾空格；原始字节未修改。精确排除这6个stdout/JUnit文件后同检查退出0，所有源码/测试/报告均通过（完整argv和原始输出在staged-check.json、staged-source-check.json）。新证据Git blob逐一等于本机原始字节，运行/fixture另绑定Git blob SHA256以区分Windows换行。四个原untracked未暂存。
