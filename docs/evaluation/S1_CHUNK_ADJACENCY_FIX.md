# S1：混合父子切块中的独立块邻接修复

2026-10-19；实现基线 `018b7bb2befd534c09da02c812944fa4f855a2de`。

## 更正真实 0434 的诊断

之前会话把上传汇总 JSON 缺少邻接字段，误读成数据库邻接字段为空。该汇总的 SELECT 没有查询 `parent_chunk_id/pre_chunk_id/next_chunk_id`，不能据此判断缺链。

真实 API Search 轨迹中，第57块 `684b1220-4546-4235-8b18-53b7d9be3baa` 的 `next_chunk_id` 已指向第58块 `956cf096-d183-4a96-adf5-75e9fd71a4e2`；前者含物理第33页 `Coding time!`，后者含第34页 Kaggle 标题。真实来源版本为 `43204b4c-5253-4106-8e6d-f43bf097708a`，文档为 `2f283a53-de65-41c1-a1f5-e8a7ea75e64d`。

因此，该次实际请求的直接阻断是 Rerank 最高分0.1093258485低于0.3阈值及0.15 Top1保底，抛出 `SearchNothing`，未执行 Merge。不能把本次代码修复宣称为已经修好该题，或声称它此前没有相邻指针。原始真实请求/回执、答案及用量不改写，也不重发。

只读核对证据：`E:\WK\run\s1-0434-section-20261019\real-check\question\evidence\*.stages.json`；独立核对摘要及 SHA 见 `E:\WK\run\s1-chunk-adjacency-20261019\actual-case-adjacency.json`。

## 实际修复

`backend/weknora_port/storage/chunks.py::prepare_chunks` 存在另一种可复现缺口：只要整份文档有任何 parent row，所有 text child 都不建邻接链，包括没有 parent 的独立短块。这会影响同一文档同时包含较长父子段落和多个独立短段落的情况。

现按原解析 `chunk_index` 排序，仅在连续且原始解析双方都没有声明 parent 的子块之间填写双向 pre/next。遇到 parent-backed child 或无法解析的 parent 引用就两侧断链，不跳过它连接后面的独立块。原有 parent 链、纯 text 链及真实 parent-child 关联保持原合同。

本次不修改正文、ContextHeader、Chunk ID、Embedding 输入、Schema、Rerank、阈值、路由或模型。生成计划仍在分配 ID 前检查 tenant/owner/KB/document/source_version。历史数据库记录不回填；改动作用于新解析的 ChunkPlan。

## 验证

新增回归覆盖：混合切块的双向链、乱序输入排序、父子区段隔离，以及实际 `merge()` 沿新指针补回相邻原文。测试中的锚点是合成的“已被 Rerank 接纳”结果；不冒充真实模型新评分或该题自动问答通过。

修复前执行新增反例：**exit1，3 failed、1 passed**。修复后执行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONIOENCODING='utf-8'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_chunk_storage.py tests/weknora_port/test_mineru_sections.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_merge_source_retention.py tests/weknora_port/test_source_evidence.py
git diff --check
```

定向测试 **exit0，131 passed**，5个既有PyMuPDF弃用警告。覆盖无效parent引用不会变成邻块锚点、已有350/850字符邻块边界、来源正文保留、KB/文档范围校验、Parent/Child、MinerU section 和 Context 相关路径。`git diff --check` **exit0**。

日志：`E:\WK\run\s1-chunk-adjacency-20261019\pytest.log` 及对应 exitcode；本轮新增 Provider/Token/UNKNOWN、数据库写入和索引改动均为0。未重新上传或提问，未启动服务。未修改用户原有未提交文件。

结果：**IMPLEMENTED / OFFLINE_PASS**；混合切块缺链已修复。新版本真实上传与模型问答 **NOT RUN**，0434 的 Rerank 低分问题仍在独立质量问题范围内。
