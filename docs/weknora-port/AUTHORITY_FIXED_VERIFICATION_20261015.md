# 给协调者 / 6luna 的固定验证交接

仅独立固定验证，不重复全仓审计、浏览器或检索/图片矩阵。代码与证据对应本报告同一提交；以 `evidence/AUTHORITY_ASSEMBLY_20261015/source-binding.json` 的 Git blob SHA256 核对。若工作副本hash与Git blob因EOL不同，用两个已列hash分别核验，不替换原始证据。

目标：`E:\RAG-Worktrees\weknora-python-port-20261013`；解释器 `E:\RAG quention\.venv\Scripts\python.exe -B`，reader `E:\WK\deps\reader`，runtime deps `var\weknora-port\runtime-deps`。所有临时目录/下载限D/E。不要暂存或修改 `.serena`、四保护文件、源码或历史证据；不运行真实模型/部署/清账。

若需要实际复跑（由协调者安排一次），先确认没有代码写入者和同标签报告，使用**全新标签**。命令存在，以下是建议的新执行，不代表已经运行：

```powershell
Set-Location -LiteralPath 'E:\RAG-Worktrees\weknora-python-port-20261013'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B docs/weknora-port/evidence/AUTHORITY_ASSEMBLY_20261015/run-check.py luna-authority-offline tests/weknora_port/test_authority_usage.py tests/weknora_port/test_send_gate.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_transport_occupancy.py tests/weknora_port/test_development_entry.py tests/weknora_port/test_document_profile.py tests/weknora_port/test_documents.py tests/weknora_port/test_native_protocols.py
& 'E:\RAG quention\.venv\Scripts\python.exe' -B docs/weknora-port/evidence/AUTHORITY_ASSEMBLY_20261015/run-check.py luna-authority-isolated --pg-env tests/weknora_port/test_authority_cli_pg.py tests/weknora_port/test_token_authority_pg.py
```

预期：离线113项exit0；隔离8项exit0。以实际输出为准；模型请求为MockTransport SIMULATED。隔离脚本会显式应用014并新增合成tenant/account/KB/任务/UNKNOWN，不删除任何历史；若只获只读验证权限，不执行第二条，改读既有 receipts/JUnit/source binding。

隔离目标必须为既有获准 PG 56423/库名 `weknora_python_port_20261013` / physical7696088403504697382，Redis56424/db15。launcher从已有隔离报告取DSN而不打印，查询当前 `eviroute-rag-redis` 的run_id；名称/身份异常立即停止，不猜旧库地址或启动/改容器。新前缀/存储在 `E:/WK/run/authority-cli-<random>`。

固定断言：

1. 两个真正标准CLI main而非tests注入factory完成正常上传、问答、引用HTTP回读、worker任务；同一authority config SHA；两worker exit0。
2. 默认无authority ready503/disabled；请求JSON不能覆盖authority；synthetic绑定fixture.invalid与代码MockTransport，approved依然拒绝，不需价格/假零价。
3. 同一logical slot不因模型/config/wire或Cheap/Expensive角色、single/batch0变化而另发；released/跨单位重用拒绝；同call跨单位竞争只有一个winner。
4. 预留实际commit后才进模型；外层事务拒绝；两连接月额竞争不超额；等待锁后撤销consent不发送。
5. 原始 input/output/cache/total的缺失/0、reported/derived总量和SIMULATED标记；缓存不重复加，流快照不求和，后续缺usage不清先前事实。
6. 原预留100、已知下界150→held150/UNKNOWN/block同事务，原reserved不改；分量超界也保守block；cancel/deadline/缺usage、NOT_SENT释放边界及幂等finish。
7. 故意杀本测试API子进程后 held保留（Windows强制退出1，不称正常退出），重启/重POST无新claim；故障行actual未知，held是预算预留不是actual。不得清除它。

源码缺口单列：生产SourceFacts逐来源接线、真实meter尚未实现，approved mode报APPROVED_METER_UNIMPLEMENTED；不是“复跑green即可生产启用”。真实carry/UNKNOWN及共享账户事实另有Owner门，不能填0。固定验证不批准延期/上线，也不对已有浏览器证据追加历史hash。
