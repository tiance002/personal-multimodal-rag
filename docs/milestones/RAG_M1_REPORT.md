# RAG-M1 — 当前版本真实 RAG 里程碑

状态：IN_PROGRESS。基线：17a0b09772246fa26b5d835d12ad8d63a332c203。
固定上游：WeKnora v0.8.2 / 3e8b0bfc80b845b2d4b2ed683994748741450a97。

## 执行清单

- [ ] 恢复正常配置路径，定位 P32/P34 解密差异。
- [ ] 四份冻结 Rerank 输入真实评分及原生过滤决策。
- [ ] 正常 Cheap / Expensive / 受控升级真实验收。
- [ ] 当前版本六套冻结子集质量结果。
- [ ] 当前版本 Edge 问答、历史恢复与多模态链路。
- [ ] 定向回归、Windows 启动说明、本地提交与最终状态。

## 已确认事实

P34 的最早失败是诊断进程缺少 cryptography：P32 在子进程启动前设置 PYTHONPATH，P34 则仅在启动后写入环境变量，未应用到 sys.path。SecretCipher.decrypt 的统一异常包装使依赖导入错误显示为 STORED_SECRET_DECRYPT_FAILED。项目已锁定 cryptography==46.0.3，既有隔离依赖目录已有该版本；本轮优先复用显式依赖路径，不改密钥、密文或历史状态。

## 权限和数据边界

沿用当前 M1 Owner 授权，使用已配置 Provider 和外发同意，有限调用逐项记录；不恢复 ApprovedTokenAuthority、账户/月度围栏、SourceFacts、Summary 排除或 image_pages 路径。旧 UNKNOWN、Gold、历史测评和原有未提交文件保持原状。

## 证据

本轮证据目录：E:\WK\run\rag-m1-20261017。历史 P34 结果保留，后续修正结论在本报告记录。
