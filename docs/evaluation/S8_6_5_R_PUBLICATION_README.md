# S8.6.5-R 公开源码与私有验收包

本分支从公开远端基线 `db917c8` 重建可发布后继，保留 S8.6.5 可观测性、生成质量修复和固定配置后台心跳调整。原本地四个提交及原始证据继续保留；本分支排除159份新证据文件，逐文件 SHA256 见 `S8_6_5_R_PRIVATE_EVIDENCE_HASHMANIFEST.json`。公开报告是脱敏衍生件。

固定生成比较的真实历史请求、文档 Context、问题、账本和当前来源证明属于私有验收包。该包没有随公开源码发布。执行工具需要调用者显式提供私有路径：

```text
python scripts/s8_6_5_generation_compare.py dry-run --package <private-package>
python scripts/s8_6_5_generation_compare.py prepare --package <new-private-package> --source-export <private-export> --ecs40-snapshot <private-snapshot> --model-inventory <private-inventory> --model-config-snapshot <private-safe-role-snapshot>
```

上述命令只说明入口，真实私有材料准备命令本轮 `NOT RUN`。`run` 继续要求独立的新 Owner grant、配置、LocalExecution 文件、case 和输出目录；显式 package 路径不构成 Provider 发送授权。

公开 `test_s865_generation_compare.py` 在 pytest 临时目录生成明确标记 `SYNTHETIC_NOT_HISTORICAL_ACCEPTANCE` 的10条案例，只验证文件哈希、ABC 渲染隔离、参数契约、缺少私有输入时拒绝，以及 mock UNKNOWN 停止与计量保留。合成 fixture 不伪装为历史 HTTP 样本或当前 ECS 模型证明。原历史测试与真实包保留在原本地分支，可由负责人按对应授权复核。

本轮仅运行受影响的该测试模块：19passed、exit0。没有因此重跑历史 ABC、真实 Provider、HTTP、PG 或正式业务验收。源码身份与 staged blob 敏感信息检查见 `S8_6_5_R_SANITIZED_TREE_AUDIT.json`。本阶段按负责人授权完成本地清洁发布提交；推送由主代理协调，尚未推送或打 Tag。
