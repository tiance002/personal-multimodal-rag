# P34：Rerank 真实评分复验与配置决策

日期：2026-10-17
状态：`P34_BLOCKED`
源码基线：`f8ec9c548e48ef565f5011b8a02a705d4bfabb7c`
固定 WeKnora：`3e8b0bfc80b845b2d4b2ed683994748741450a97`

## 结论

四个 P32 请求体均通过字节级 SHA-256 校验；候选顺序、目标 index 及租户隔离库中 120 条来源 Chunk（含 Omni 图片子块的 image enrichment）全部重建一致。正式评分没有开始：使用正常配置路径严格解密 SiliconFlow Rerank 凭据时返回 `STORED_SECRET_DECRYPT_FAILED`。没有发送任何供应商请求。

因此，本轮没有新的 relevance score、模型排名或可重放的 0.3/0.15 原生筛选结果。P33 的历史 UNKNOWN 均保留。决策为 **A：暂时保留现有模型和阈值，不作生产调整**；这是在没有 P34 新分数时的保守维持，不代表现有阈值已被证明最优，也不代表历史被过滤候选的分数已知。

## 冻结输入核验

输入来源为 P32 Provider 请求捕获和 P33 固定候选映射。每个输入包含 30 个候选，捕获边界为 `HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE`，Provider/Model 为 `siliconflow` / `BAAI/bge-reranker-v2-m3`。重建的规范 JSON 字节与原记录的 wire SHA 及字节长度相同；数据库来源 Chunk 和图片派生信息重新经当前租户、KB Scope 回读后，120/120 个候选文本 SHA 均匹配。

| 样本 | 实际 Rerank Query | 历史请求 ID | 冻结请求 SHA-256 | 字节 | 候选数 | 目标 Chunk / 输入 index | 当前来源文本核验 |
|---|---|---|---|---:|---:|---|---|
| MML row-0434 | `PPT中'coding time'部分介绍的训练数据集是什么` | `ba4ebadf-12cf-4c38-a30a-576c24fadf57/rerank` | `8c730c5e6dd03f3c18b5a460861d43ebd1a1b477326bb906ca36fbfd8e401fe9` | 14,014 | 30 | `085643dc-5ebb-4b8a-b7f1-75427da40015` / 14 | PASS |
| Hotpot `5a7631` | `Jeanne Marie Beaumont 2007-2010 研讨会 举办地点` | `02074de1-c0a0-4df7-9ae2-5c62b7ff5586/rerank` | `954a7767c871336f1dfe66874a40404d4f56c83ff67eb68d52a17eb6f9b9f4a7` | 12,045 | 30 | `31417c7c-7abf-4723-956a-74bf7ee14683` / 14 | PASS |
| Omni page007 | `课件中线性方程组的系数矩阵，以及增广矩阵最后一列的三个数` | `1a4f13e2-dfd2-4328-9c3e-8b6a5f2d0813/rerank` | `499ec3fdf906de030f76b941d1aab48b7350e96f9a398de56286c8e09f0f5e4b` | 97,363 | 30 | `51316a20-67fd-4732-b520-c049999933d0` / 4 | PASS |
| MML row-0959 | `用于远距离通信的介质有哪些` | `71d59b6e-6dff-4431-945a-3b1a800b5d89/rerank` | `0b73ae3540ae1209b17810e05f94b95da17834c03b1bde0667c8ad8c9e0aa0f7` | 13,618 | 30 | `df6098cb-68e6-4546-b9e0-8682b4bf19e9` / 1 | PASS |

请求文件来自 `E:\WK\run\p32-20261017\evidence-332\provider-requests\`。逐条完整候选映射及来源 Hash 保存在 P34 预检 JSON。输入可重现不表示本轮调用了模型。

## 阻断与安全边界

P34 预检核对了隔离 PostgreSQL 身份（database `weknora_python_port_20261013`、system identifier `7696088403504697382`）、tenant 332、四个 KB 的外发同意状态和模型配置中的 Provider/Model 身份。没有修改持久 KB 同意、模型配置或账本。P31 Token Authority 仍为已撤销状态，没有恢复、绕过或写入新额度。

最早失败点是应用正常的 `app.cipher.decrypt(stored_key, strict=True)`。错误为 `STORED_SECRET_DECRYPT_FAILED`，位于模型发送前；这说明当前保存的密文无法由现行配置路径解开。现有证据不能区分 master key 不匹配与密文损坏，报告不推断具体成因，不读取或输出密钥明文，也没有尝试其他密钥、轮换、重新登记或绕过 SendGate。

云端开关只按既有 P32 隔离运行配方在本次进程中启用，用于核对正常配置路径；KB 持久同意保持原值。由于凭据解密失败，实际 Provider 调用为 0。没有发送 Embedding、Cheap、Expensive、DeepSeek 或其他请求。

## P34 与 P33 结果对照

| 样本 | P34 新分数 / 排名 | P34 原生过滤回放 | P33 已知事实（历史，不是本轮响应） |
|---|---|---|---|
| 0434 Kaggle | `NOT_RUN` | `NOT_RUN` | Search rank 15；目标曾进入 30 条 Rerank 输入但最终保留 0 条；原始分数、Provider 返回 index 和过滤分支仍为 UNKNOWN |
| Hotpot Franconia | `NOT_RUN` | `NOT_RUN` | Search rank 15；目标未保留，另有 2 条候选保留；目标分数仍为 UNKNOWN |
| Omni OCR 数字 | `NOT_RUN` | `NOT_RUN` | OCR Search rank 5；目标未保留，只保留 Summary；Summary 历史分数 0.7660，不是 OCR 分数，目标分数仍为 UNKNOWN |
| 0959 无线电 / 微波 | `NOT_RUN` | `NOT_RUN` | Search rank 2；P32 历史保留模型分数 0.5237，答案证据完整。这是既有成功对照，不是 P34 新调用 |

历史样本表明 Rerank 曾在三道失败样本的输出边界丢失必要 Chunk，也曾在 0959 保留有效证据；但无法从 P33 记录推出前三题实际低于 0.3、未达到 0.15 保底，或具体属于模型排序问题。由于 P34 无响应，本轮不比较 Summary 与 OCR 新分数、不标定阈值，也不声称正负例模型排名。

## 调用、Token、延迟与测试

| 项目 | 结果 |
|---|---|
| 新真实 Rerank 请求 | 0（上限 4） |
| 其他模型请求 | 0 |
| Provider Token usage | `NOT_RUN`；没有请求和响应，不能填入模型 usage |
| 延迟 | `NOT_APPLICABLE`；没有模型请求 |
| 0.3 / 0.15 产品筛选函数回放 | `NOT_RUN`；无新的有效 `RerankResponse` |
| 输入重建 | PASS；4 份 wire SHA、顺序与 120/120 来源文本 SHA 对齐 |
| 生产代码策略 | 未修改阈值、模型、检索、Prompt 或路由 |

执行记录：

| 命令 | 退出码 | 结果 |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "from pathlib import Path; compile(Path('scripts/p34_rerank_frozen.py').read_text(encoding='utf-8'), 'scripts/p34_rerank_frozen.py', 'exec')"` | 0 | 诊断脚本语法编译通过 |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/p34_rerank_frozen.py` | 2 | 输出 `P34_BLOCKED`；错误在 `backend/weknora_port/crypto.py::SecretCipher.decrypt(strict=True)`，Provider calls=0。通过 PowerShell 捕获子进程退出码确认。 |

预检记录：`E:\WK\run\p34-rerank-20261017\preflight.json`。机器可读摘要：[`P34_RERANK_SCORE_DECISION.results.json`](P34_RERANK_SCORE_DECISION.results.json)。本轮只运行脚本语法编译及定向只读预检；没有执行全项目测试。

## 配置决策与后续条件

选择 **A（暂时保留现有模型及 0.3 / 0.15 配置）**，理由是没有取得任何 P34 新响应分数，当前样本不足以据此修改生产策略。此决定不认可现有阈值已最优，也不否定历史 Rerank 质量疑点。

解除阻断需要在正常配置/凭据恢复流程中使本项目当前 SiliconFlow Rerank 凭据可严格解密，并保留当前被拒绝状态作为证据。完成该项后，才可由有效凭据与 SendGate 对同一批冻结输入各发送一次；不能把本报告中的 P33 UNKNOWN 改写成 P34 分数，也不能在网络结果 UNKNOWN 时重发。

本轮生产代码修改：无。唯一新增脚本为一次性冻结输入预检与单次发送器 `scripts/p34_rerank_frozen.py`；没有本地提交生产实现。
