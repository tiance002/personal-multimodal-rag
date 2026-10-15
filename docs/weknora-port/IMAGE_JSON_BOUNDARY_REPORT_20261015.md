# 父聚合 JSON 边界修复 — 2026-10-15

1. **目标与改动**：基线42c1a278506d26bb0eb7df4cba89ce07e1260abe，固定上游3e8b0bfc80b845b2d4b2ed683994748741450a97。仅 `retrieval/merge.py` 新增父聚合专用 `_parent_image_infos` 及两个私有辅助定义，collector.add改用它；共享rerank/Context解码、seq、SQL/Scope、两层批量、只填空、版本锚点及引用规则未动。移除新定义并把调用名改回后，整个merge AST与基线相同。

   解码保留object pairs及重复键输入顺序，按Go大小写折叠匹配六个tag；后出现的有效值覆盖（即使前键是精确小写），primitive null不覆盖旧值。只接受字符串/真正整数，坐标范围为该固定64位Go int的[-2^63,2^63-1]；任一已知字段类型/范围错误使整个子块数组失效，绝不截断/夹值。未知Owner扩展忽略于新父聚合，child原串及非空parent仍原样保存。Unicode仅按这六个ASCII tag需要的simple-fold（含长s）处理，不采用会展开连字的full casefold。不声明一切JSON/Unicode字节边界等价。

2. **执行命令**：cwd为独立树，解释器 `E:/RAG quention/.venv/Scripts/python.exe -B`，缓存/临时目录D/E，未安装依赖。精确argv/环境目录/原始stdout/stderr/exit/JUnit在 `evidence/IMAGE_JSON_BOUNDARY_20261015`：

   - `scripts/weknora_port_image_json_oracle.py`：退出0，复用已绑定的原Go collector/enrich/type程序，真实Go1.26 encoding/json执行30组case。覆盖大小写、重复精确/折叠键双向顺序、null前后、错误值即使后有合法重复仍整数组失败、正负signed64边界/合法0/负数、上下溢出、float/exponent/bool/string错误、坏第二元素、未知扩展、simple-fold及不展开连字。
   - `run-check.py red tests/weknora_port/test_image_json_boundary.py`：退出1，14失败/20通过。原始新反例保留。
   - `run-check.py green tests/weknora_port/test_image_json_boundary.py tests/weknora_port/test_search_image_completion.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_hybrid_storage.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_reference_metadata.py`：退出0，158通过；30Go案例全部经完整hybrid_search对照，并断言child原串和Evidence字面信息。
   - `run-check.py pg --pg-env tests/weknora_port/test_search_image_completion_pg.py`：退出0，1通过，实际ASGI路由/图片worker/PG/Redis/search→Evidence→Context→引用HTTP200/JSONB/history，模型仅MockTransport。
   - `run-frozen-search.py`：退出0，原实际Go冻结语料10结果/18共同字段和image_info一致，4child原串不变；12索引行/10源行未变化，read-only、0写/DDL/模型。score/tie顺序未重新断言。

   新case fixture和原始Go程序、Go SDK decode.go/fold.go/VERSION、改动及受保护源码均在source-binding.json中SHA绑定。首次浏览器交接只读查找的frontend/src/App.tsx不存在；已定位实际frontend/src/app/App.tsx，不影响任何测试。未实现的命令不得报告通过。

3. **结果**：命名JSON边界为SOURCE_READ / IMPLEMENTED / OFFLINE_PASS；既有实际链路最小回归ISOLATED_INTEGRATION_PASS。无真实供应商、无真实代理、无浏览器验收、无CUTOVER_PASS或整体Goal验收；Office/Proxy没有重开。配置优先级、0.35路由和原升级开关未变。

4. **风险与遗留**：本轮仅对父聚合所需JSON语义对齐；共享child解码/原始字段不重写。类型失败整数组拒绝，权限/取消/版本异常原防线不变。融合tie仍UNDEFINED_FUSION_TIE_ORDER。四个既有untracked文件哈希匹配且不暂存；旧业务DB/卷/配置/密钥、历史/UNKNOWN保全。浏览器完整流程按Owner安排交其他Codex执行，本轮只读核实交接事实。

5. **版本与下一步**：最终本地提交SHA见Git最终输出。父级独立审查本提交；[浏览器事实](BROWSER_FACTS_20261015.md)仅列现存入口、运行模式和证据，不代表已启动或浏览器通过。禁止真实模型/Push/部署。

暂存检查：完整diff-check退出2；原始pytest失败输出尾空格不改字节，精确排除2个原始stdout/JUnit文件后源码/测试/报告检查退出0。原始argv/输出在staged-check.json和staged-source-check.json；新证据Git blob等于原始字节，另绑定源码Git canonical bytes。
