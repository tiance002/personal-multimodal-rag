# M4 第一批真实 Qwen 配对回答评测

## 结果与范围

- 状态：COMPLETED_FIRST_25_AND_STOPPED；配对完成 25/25，未完成 0/25。
- 已启动生成 API 调用：50/50；唯一调用 50，重试 0，warmup 0；未安排剩余 35 题。
- 生成阶段耗时 204.71 秒；相邻成功回答最大间隔 10.8 秒（阈值 180 秒）；首批 25 题完成后停止。
- 预注册固定 SciFact 5、MIRACL-ZH 10、LongBench-ZH 10；8 对双盲审核样本在生成前冻结，人工审核完成 0、待审 8。事实准确性、完整度、证据支持与引用准确性均为 NOT_EVALUATED。

## 冻结配置

- A：Vector-only；C：M3 weighted RRF，vector=0.8、keyword=0.2；top_k=5、candidate_k=32。
- Qwen：qwen3.5:4b，digest 2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd。参数：temperature=0、seed=20260929、num_predict=512、think=false、stream=false。
- Prompt 模板 SHA-256：139671bfaa3ea7f6ee3881d53559c7e71ffc105e1796a14f12359f21f989cd8b；prereg SHA-256：934cd6ffa7f22b5f01ca060541c15c9e1315090477faf156aa8843fd80b97ff7。同一模板、同一引用格式，A/C 交替调用顺序。首个回答计入计分，无 warmup。

## 分数据集统计

| 数据集 | 配对 | 配置 | 输入 Token 合计 | 输出 Token 合计 | 总 Token 合计 | 平均延迟 ms | P50 ms | EM 均值 | 字符 F1 均值 |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| longbench-zh | 10/10 | A | 33555 (10/10) | 1496 (10/10) | 35051 (10/10) | 4478.97 | 3798.05 | 0.0 | 0.415 |
| longbench-zh | 10/10 | C | 32842 (10/10) | 1408 (10/10) | 34250 (10/10) | 4470.98 | 3190.95 | 0.0 | 0.4874 |
| miracl-zh | 10/10 | A | 7473 (10/10) | 753 (10/10) | 8226 (10/10) | 2051.43 | 1864.7 | NOT_EVALUATED | NOT_EVALUATED |
| miracl-zh | 10/10 | C | 9534 (10/10) | 864 (10/10) | 10398 (10/10) | 2405.1 | 1909.5 | NOT_EVALUATED | NOT_EVALUATED |
| scifact | 5/5 | A | 5737 (5/5) | 308 (5/5) | 6045 (5/5) | 4097.72 | 2860.2 | NOT_EVALUATED | NOT_EVALUATED |
| scifact | 5/5 | C | 6222 (5/5) | 504 (5/5) | 6726 (5/5) | 2847.32 | 3032.7 | NOT_EVALUATED | NOT_EVALUATED |

## LongBench-ZH 逐题结果

公开参考答案用于 normalized EM 与 Unicode 字符级 F1；引用标签在评分前剔除。逐题分数、输入/输出 Token、延迟和 Context Hash 也在机器结果 JSON 中。

| QID | M3 Context recall C−A | EM A/C | Char F1 A/C | Char F1 C−A | Input Token A/C | Output Token A/C |
|---|---:|---:|---:|---:|---:|---:|
| multifieldqa_zh:85c09503fc369e433c8a3421925ca49dedaf318296d69239 | 0.0 | 0.0/0.0 | 0.2857/0.2857 | 0.0000 | 3514/3514 | 52/52 |
| dureader:38fd53c130741b601894b7a17f13334273cf94806105b2fc | 0.0 | 0.0/0.0 | 0.8889/0.8889 | 0.0000 | 3570/3570 | 22/22 |
| multifieldqa_zh:d629526ca57cedd97bf583c643ba3ae7f1ec858c832f7321 | 0.0 | 0.0/0.0 | 0.8824/0.8824 | 0.0000 | 3197/3197 | 39/39 |
| dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7 | 0.0 | 0.0/0.0 | 0.4230/0.4306 | 0.0076 | 3174/3174 | 312/328 |
| multifieldqa_zh:52429a8971455e70b8ff4c98de79a7399ca35295a476e1f8 | 0.0 | 0.0/0.0 | 0.1345/0.8000 | 0.6655 | 3564/3236 | 225/26 |
| dureader:fac00bf870690472fea62b70749d61c9e24980a8e290df34 | 0.0 | 0.0/0.0 | 0.3105/0.3105 | 0.0000 | 2852/2852 | 142/142 |
| dureader:0b704eaa04aa78dd365d7f50ea3e9edad9bd6a5dc3149f42 | 0.0 | 0.0/0.0 | 0.1440/0.2083 | 0.0643 | 3373/3215 | 245/236 |
| dureader:bdde21dea30258a71f543f49a4c73aa0f56fe0efc631088e | 0.0 | 0.0/0.0 | 0.2444/0.2314 | -0.0130 | 3229/3229 | 286/390 |
| dureader:aaca9663ac31aee4f90704fe6cd67fdedf8d3710279a720d | 0.0 | 0.0/0.0 | 0.3238/0.3238 | 0.0000 | 3289/3289 | 142/142 |
| multifieldqa_zh:d96e8e188e74cee444a4ad05c931df12a05734ecdeb23dce | 0.0 | 0.0/0.0 | 0.5128/0.5128 | 0.0000 | 3793/3566 | 31/31 |

- EM：C 优于 A 0，持平 10，低于 A 0；平均 C−A=0.0。
- 字符 F1：C 优于 A 3，持平 6，低于 A 1；平均 C−A=0.0724。
- M3 context source recall@5 与 LongBench 答案质量逐题对照为描述性关联，不作因果结论；本批 10 题的 C−A context coverage 均为 0。SciFact/MIRACL 无真实 Gold Answer，不伪造答案，语义正确性和完整度均 NOT_EVALUATED。

## 引用回读与支持

READBACK_PASS 只表示输出的 [E#] 回映到冻结 Context 的 chunk/version/source version/content SHA；NO_CITATIONS、READBACK_FAIL 分开计数。回读成功不等于语义支持。

| 数据集 | 配置 | READBACK_PASS | READBACK_FAIL | NO_CITATIONS |
|---|---|---:|---:|---:|
| longbench-zh | A | 10 | 0 | 0 |
| longbench-zh | C | 9 | 1 | 0 |
| miracl-zh | A | 10 | 0 | 0 |
| miracl-zh | C | 10 | 0 | 0 |
| scifact | A | 3 | 0 | 2 |
| scifact | C | 4 | 0 | 1 |

## Hash 与来源

- 项目 Git SHA：a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b；M3 检索 Git SHA：9303938c20a118cf022fd5934cbc8c4392452548；M4 runner SHA-256：d0133c3f956def489e59548216be02c7d2dfc80ccec70f0679dd7740a15e204a。
- M3 ContextBuilder/public trace 源码 SHA-256：7a66c0589ac94edc818d095a5813b08360ecdaad0afb99f7fc2770e5d34911c2 / 388ff03e4e5f97109eed72175911c7b3d252a7f3ea5f697623f7d1662d2c8e32。
- M3 最终报告 SHA-256：3f0a05975785824b7f37c14a0af7bdab010a21f9efcef7658a25da4772ebd0fb；M3.5 分析 JSON/脚本 SHA-256：ae53e1a4cac2e33abbd649ab64c0edb05466e6131ae17e7b38890714593a1590 / 841c3880f2ce2932dcd8be1a6343e3829fc95b623953532a3aa10753f234c7db。
- M1/M2 manifest SHA-256：78847a429048e412f018089bf76ced269705d901ee79edcdfb675a81cf701fa3；M3 manifest/results SHA-256：06a40ad0afef526e21f824f5f32e0d115204304522915a4ef402000aa8c0c8f4 / eaf0bdeed2beb7346b3dbad56b2227baddfe44925124fea7331eb4365b83cb1c。
- 全部 50 个 Context 哈希根：730dd99011a0fec9c078909ffe5fa7c6016ff9b1a709c61474941db8f6c32be5；脱敏结果 SHA-256：5cba58f0ab7b7821ac55495f40ec808125d5e5a8c1c634834034a4cf71e46e1d。
- 数据集版本、adapter manifest/cases SHA-256、逐 QID Context 与 evidence Hash 见机器结果 JSON。

## 执行命令与退出码

| 命令 | Exit code | 结果 |
|---|---:|---|
| docker start rag-eval-trust0928-db-clone-20260929t091523z | 0 | validated clone started; original untouched |
| PowerShell read-only database preflight; DSN loaded from Windows User environment scope; .venv\Scripts\python.exe - | 0 | database, schema and index counts matched M3 identity |
| .venv\Scripts\python.exe -m py_compile D:\RAG-Public-Bench\runs\m4-qwen-paired-first25-20260929\m4_runner.py | 0 | runner syntax valid |
| .venv\Scripts\python.exe D:\RAG-Public-Bench\runs\m4-qwen-paired-first25-20260929\m4_runner.py prepare | 0 | 50 inputs frozen; generation calls 0 |
| .venv\Scripts\python.exe D:\RAG-Public-Bench\runs\m4-qwen-paired-first25-20260929\m4_runner.py generate | 0 | 50 unique calls and 25 checkpoints |
| .venv\Scripts\python.exe D:\RAG-Public-Bench\runs\m4-qwen-paired-first25-20260929\m4_runner.py score | 0 | offline scoring complete |
| PowerShell read-only postflight; Python stdin script; DSN value not printed | 0 | 50 contexts, usage, M3 hashes and model digest verified |
| docker stop rag-eval-trust0928-db-clone-20260929t091523z | 0 | clone stopped; volume preserved; original remains stopped |

## 隐私与限制

- M3/M3.5 原产物哈希保持不变；本轮无重检索、Embedding、下载、Locked、Judge、云端模型、ECS 同步或生产默认策略修改。仅启动已验证 clone，使用只读事务；原容器未启动、未挂载、未修改；完成后 clone 已正常停止且卷保留。
- 原始问题、Chunk 文本、完整回答、凭据和数据库内容未包含在此公开报告或脱敏 JSON。双盲人工审核表与原始回答只留在外部私有运行目录。
- 本轮按需求报告 normalized EM 与 character-F1。LongBench 官方脚本将 MultiFieldQA-ZH 映射到中文 QA-F1、DuReader 映射到中文 ROUGE-L；本结果不是官方 leaderboard 分数，参见 [官方 eval.py](https://github.com/THUDM/LongBench/blob/main/LongBench/eval.py) 和 [官方 metrics.py](https://github.com/THUDM/LongBench/blob/main/LongBench/metrics.py)。
