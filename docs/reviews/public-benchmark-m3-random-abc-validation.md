# M3 Development 随机样本 A/B/C 检索复核

**状态：BLOCKED（检索前）** · 更新：2026-09-29

## 1. 目标与改动

本次目标是在已核验的克隆 PostgreSQL 实例上，以事先固定的 Development 样本复核 A/B/C 检索。M1/M2 历史代码和报告保留在原提交；本次代码位于独立分支 `codex/m3-random-abc`。

已完成的源码提交 `654012054bef5056663987bce683179c73735c70` 包含：

- 只按 Development QID 和 `SHA256(dataset|qid|seed)` 生成样本，排除 M1/M2 与 M0 诊断样本；固定 SciFact 20、MIRACL-ZH 30、LongBench-ZH 10，seed `20260929`。
- A 独立 Vector-only、B 无权重 Hybrid RRF、C Keyword 0.2 / Vector 0.8；三者固定 candidate depth 32、top_k 5、RRF k=60、chunk 1200/120、context 8,000 字符。
- 对向量缺失、向量降级、来源集合变化、配置变化和融合列表缺失实行拒绝评分；样本哈希、源码身份、容器/卷/端口、数据库与索引身份、HNSW 有效状态门禁。
- 每个 QID 独立复用成功的查询 Embedding，并轮换 ABC/BCA/CAB；限制最多 180 次检索、60 个成功查询 Embedding、20 分钟，5 分钟无进度停止；不重试、不续跑、不扩样。
- 结果记录不保存问题文本；不生成答案或调用 Judge。实际 Token 计数没有采集时标记 `NOT_MEASURED`。

M1/M2 历史执行 SHA 是 `241d2c7ebdc4911e92a18bdc1e48307d85c87503`；其历史执行 Tree/Patch 未保存。后续源码保存提交 `2439c806ff28df1ed254ead58ecaca1036973638` 仅标识后续源码保存版本。

## 2. 执行命令

| 命令/操作 | 退出码 | 结果 |
|---|---:|---|
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest eval_center/tests/test_public_m3_abc.py eval_center/tests/test_readonly_public_index.py eval_center/tests/test_query_cache.py eval_center/tests/test_public_trace.py backend/tests/test_opt_in_retrieval_modes.py backend/tests/test_retrieval_execution_record.py -q --tb=short -p no:cacheprovider` | 0 | 31 passed，6 个依赖弃用警告 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m py_compile eval_center/public_m3_abc.py eval_center/readonly_public_index.py eval_center/tests/test_public_m3_abc.py eval_center/tests/test_readonly_public_index.py; git diff --check` | 0 | Python 编译及工作区 diff 检查通过 |
| `git diff --cached --check` | 0 | 提交前无空白错误 |
| `git commit -m "feat(eval): add preregistered M3 random ABC review"` | 0 | 创建提交 `654012054bef5056663987bce683179c73735c70` |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m eval_center.public_m3_abc freeze --data-root 'D:/RAG-Public-Bench' --m1-m2-manifest 'D:/RAG-Public-Bench/runs/m1-m2/20260929T001003Z/manifest.json' --output-dir 'D:/RAG-Public-Bench/runs/m3-random-abc/prereg-20260929-seed20260929'` | 0 | 冻结 60 个 QID；样本文件只在本机基准目录 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -c "import json; from pathlib import Path; from eval_center.public_m3_abc import verify_frozen_sample; p=Path(r'D:/RAG-Public-Bench/runs/m3-random-abc/prereg-20260929-seed20260929/preregistration.json'); c=p.with_name('cases.jsonl'); x=verify_frozen_sample(p,c,Path(r'D:/RAG-Public-Bench'),'53d2e48d9a9857cb27843b53f2c73bfe16fcf5aacbc410b3625585a0c0f12fc0'); print(json.dumps({'status':'FROZEN_VERIFIED','preregistration_sha256':x['preregistration_sha256'],'cases_sha256':x['cases_sha256'],'qid_set_sha256':x['qid_set_sha256'],'counts':{k:len(v) for k,v in x['qid_sets'].items()}},sort_keys=True))"` | 0 | 样本数量、哈希、排除集合、配置和干净源码身份一致 |
| `docker inspect rag-eval-trust0928-db-clone-20260929t091523z` | 0 | 容器 ID `c705dd58ee38a637f2bddcd188a175fb98612b74c74b3d9798ac9de60daf5b54`；独立卷同名；仅映射 `127.0.0.1:25437` |
| `docker start rag-eval-trust0928-db-clone-20260929t091523z` | 0 | 仅启动克隆容器 |
| `docker exec rag-eval-trust0928-db-clone-20260929t091523z pg_isready -U postgres -d postgres` | 0 | PostgreSQL 接受连接 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -c "from sqlalchemy import create_engine,text; u='postgresql+psycopg://postgres@127.0.0.1:25437/postgres'; e=create_engine(u,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'}); c=e.connect(); print({'database':c.execute(text('select current_database()')).scalar_one(),'transaction_read_only':c.execute(text('show transaction_read_only')).scalar_one()}); c.close(); e.dispose()"` | 1 | 服务返回 `fe_sendauth: no password supplied`；未执行数据查询 |
| 克隆 Unix socket 检查：`psql -U postgres -d postgres`；`psql -U rag -d postgres` | 1 / 1 | 两个角色均不存在；均未执行数据查询 |
| `docker stop --time 30 rag-eval-trust0928-db-clone-20260929t091523z` | 0 | 正常停止（Docker 提示 `--time` 将改名为 `--timeout`） |
| 停止后 `docker inspect rag-eval-trust0928-db-clone-20260929t091523z` | 0 | 克隆容器状态为 `exited` |

本机当前进程没有 `RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL`；克隆容器未设置 `POSTGRES_USER` 或 `POSTGRES_PASSWORD`；标准用户 pgpass 文件不存在。Docker 检查显示克隆数据目录挂载为 `RW=True`，评测连接尚未建立，因此本轮尚未验证 PostgreSQL `default_transaction_read_only=on`。没有读取原容器或原卷来取得凭据。

## 3. 结果

- **源码与定向测试：PASS**。Tree `23cc877b0b6985d38f647d715e19b58b875ee03d`；Patch SHA256 `a39aa8dd6a940b719be35d4e05d2045b2264d16ba921a03ebb6204c184c1e9c0`；提交时工作树干净。
- **Development 预注册：PASS**。预注册 SHA256 `53d2e48d9a9857cb27843b53f2c73bfe16fcf5aacbc410b3625585a0c0f12fc0`；cases SHA256 `07f6937c80acaed41fa388a47650c889ca3ff5e3281cd4522429ecdcc1233135`；QID 集合 SHA256 `9962b1af390c5582b9a92a4a161f1f3402cce65925eca40da68a65dddd193f9a`。
- **克隆容器身份/就绪：PASS**。容器、独立卷和 `127.0.0.1:25437` 映射匹配；仅克隆被启动并正常停止。
- **克隆数据库只读身份及索引现场复核：BLOCKED**。TCP 认证需要当前不可用的密码；没有完成数据库身份、只读事务、HNSW 与索引指纹复核。因此不能判断本次连接失败是否仅为凭据问题，也不能报告索引当前缺失。
- **A/B/C 检索：NOT RUN**。检索尝试 0；查询 Embedding 0；语料 Embedding、新索引、下载、生成、Judge、Locked 查询和数据库评测写入均为 0。本次没有模型调用或评测结果。

冻结配置与样本记录在 `D:\RAG-Public-Bench\runs\m3-random-abc\prereg-20260929-seed20260929\`，不在 Git 仓库中。该目录应作为后续恢复的唯一预注册输入。

## 4. 风险与遗留

运行器要求一个能连接克隆三个评测数据库的现有数据库账号，并强制使用 `127.0.0.1:25437` 与每连接 `default_transaction_read_only=on`。目前没有此账号的本机会话配置；尝试改角色、重置密码、改 `pg_hba.conf`、恢复数据库或重建索引都会越过本次只读边界，故均未做。

恢复时请在本机安全环境设置 `RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL`，指向克隆的 `postgres` 管理库并使用克隆中已存在、具备所需只读权限的账号。不要把密码贴入聊天或写入仓库。凭据可用后，先复跑数据库身份/只读/HNSW 门禁；只有全部匹配预注册身份时才开始 180 次上限内的检索。若账号不存在或现场指纹不同，应继续停止并报告，不修复、不重建。

## 5. 版本与下一步

- 分支：`codex/m3-random-abc`
- M3 运行器提交：`654012054bef5056663987bce683179c73735c70`
- 验收：代码与定向测试通过；端到端评测未运行，未验收、未打 Tag、未部署。
- 下一步：补充克隆库现有账号的本地连接配置后，使用上述固定预注册继续 G2；不要重新抽样。
