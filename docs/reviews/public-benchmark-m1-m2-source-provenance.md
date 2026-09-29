# M1/M2 源码与 PostgreSQL 存储溯源

日期：2026-09-29。本文补充 M1/M2 development 诊断运行 20260929T001003Z 的源码提交和只读存储调查；它不更改原实验数据，也不把后续提交冒充为历史运行源码。

## 源码与历史记录

- M1/M2 完整源码和定向测试已提交到 codex/public-benchmark-m1-m2，源码提交为 2439c806ff28df1ed254ead58ecaca1036973638，父提交为原历史基线 241d2c7ebdc4911e92a18bdc1e48307d85c87503。历史基线没有被 amend 或替换。
- 新提交 Git Tree OID：592cdc3f3e6cb83dcffd8f2379e8ecb4f916f691。
- 父提交至新提交的二进制 Git Patch SHA-256：96de8ca910ed291ea6a29e6e59102d50cd78d2e35271cf6670f79fdea2c3a04a。
- 新提交包含 M1 trace/cache/只读索引、M2 检索与上下文 trace、来源 provenance，以及六个定向测试文件；共 15 个代码/测试文件。
- 原始运行 manifest 记载 Git SHA 241d2c7ebdc4911e92a18bdc1e48307d85c87503。当时源码工作树未提交；同时期报告记录源码工作树有改动。历史运行没有保存 Tree OID 或 Patch SHA，因此无法事后证明其精确源码快照。上述 2439c80… 是本次保存并发布的后续源码提交，不能当成历史执行 Tree/Patch Hash。
- 同时期报告保留的运行调用为：

  run_m1_m2(data_root=D:\RAG-Public-Bench, output_root=D:\RAG-Public-Bench\runs, database_urls=<本地临时环境变量>)

  这是报告中的函数调用签名；确切的 shell 启动行没有留存。凭据值没有复制到报告或仓库。
- 原 manifest.json 和 cases.jsonl 未修改，SHA-256 分别为 78847a429048e412f018089bf76ced269705d901ee79edcdfb675a81cf701fa3 与 0b276d8071f41a946487094ed43baad32d37d61e983e5e31ba0ec69454617723。本地运行目录新增 manifest.enriched.json 与 manifest.provenance.json，把原始内容与后续源码身份、可追溯命令及验证记录并列保存。

## 验证记录

历史同时期报告记录：M1/M2 执行状态 COMPLETED，60 qid、180 retrieval，237.942 秒；query embedding 60 次、cache hit 120 次，corpus embedding / 新索引 / Qwen / Judge 均为 0。此前定向测试为 11 passed。

本次实际执行的命令：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q eval_center/tests/test_runtime_provenance.py eval_center/tests/test_public_trace.py eval_center/tests/test_query_cache.py eval_center/tests/test_readonly_public_index.py backend/tests/test_context_trace.py backend/tests/test_opt_in_retrieval_modes.py
```

退出码 0：16 passed, 6 warnings in 4.98s。警告为依赖弃用提示。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m py_compile eval_center/public_m1_m2.py eval_center/readonly_public_index.py eval_center/public_trace.py eval_center/query_cache.py eval_center/runtime.py backend/app/application/context_builder.py backend/app/application/retrieval.py backend/app/domain/fusion.py
```

退出码 0。git diff --cached --check 退出码 0；提交前 staged patch 凭据扫描结果 CLEAN。源码分支已推送到 GitHub，push 退出码 0。

## PostgreSQL 与索引存储只读诊断

| 检查项 | 证据 | 结论 |
|---|---|---|
| Docker CLI/Engine | Docker Desktop server 29.8.0 可响应；Windows com.docker.service 显示 Stopped | Docker 元数据可查询；服务状态不能单独代表数据库容器状态 |
| 评测 PostgreSQL 容器 | rag-eval-trust0928-db，镜像 pgvector/pgvector:pg16，状态 exited，退出码 255 | 需要启动容器才能读取其 PostgreSQL catalog；本次没有启动 |
| 容器连接 | DB 名 postgres；原宿主端口 127.0.0.1:25436 → 5432/tcp | 评测库端点和本机 5432 不同 |
| 数据 Volume | 容器仍关联原有 Docker Volume，挂载目标 /var/lib/postgresql/data；Volume 元数据仍存在 | 未发现 Volume 被删除；仅凭 Volume 注册信息不能证明向量索引完整 |
| Windows PostgreSQL | 服务 study-plan-postgresql 正在运行，监听 127.0.0.1:5432，数据目录 E:/pgsql/data；只读 catalog 查询返回 11 个非评测数据库 | 这是另一实例；连到 5432 会检查错误实例 |
| 文件型 indexes 目录 | D:\RAG-Public-Bench\indexes 为空；本次运行 manifest 记录了三个既有 index identity | 文件目录为空不等同于 PostgreSQL Volume 中索引缺失；停机时无法只读确认索引表和向量内容 |

据现有证据，情况是“评测容器已停止、5432 是另一实例、原评测 Volume 仍登记”。是否只是索引缺失尚未证实。本次没有删除数据库或 Volume，没有下载语料、创建容器、启动服务、改连接配置或重建索引。

## A/B/C 新样本决定

本次不启动新的随机 Development A/B/C 样本。启动停止的评测 PostgreSQL 可能触发数据库恢复写入，而且目前还不能验证原索引；这超出本轮只读检查和“不恢复/重建”的边界。原始 M1/M2 完成结果保留；新样本状态为 NOT RUN。后续只有在确认原 Volume 可安全恢复并能只读验证索引后，再执行预先固定的随机样本。
