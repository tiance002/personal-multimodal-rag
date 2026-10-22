# S8.6.5-R 发布敏感信息审查

1. **目标与改动**：对远端基线 `db917c8a535b13b96ffd9ffbda18669025d5b3b3` 至原本地 HEAD `46be011ae242c0691e6864eeeef88d0cde81d6dc` 的四个未推送提交做聚焦扫描。共199个变更路径、159个证据路径、160个唯一历史 Git blob；包含源码、配置、测试、日志和固定生成包。只新增本审查报告及私有审查输出，没有修改原始源码、证据或历史。此结果不代表全仓库安全审计。

2. **执行命令**：`git rev-parse origin/codex/s8-6-adaptive-retrieval-20261021`、`git diff --name-only db917c8..HEAD`、私有 `scan.py` 扫描、历史测试 fixture 的 AST/hash 比较均 exit0。一次可选 JSON 结构读取因 Windows 默认字符编码 exit1；显式 UTF-8 重读 exit0。仅进行可信审查脚本和 Git 只读操作；业务测试、ECS、网络和模型调用均 `NOT RUN`。逐条命令与全部路径/blob/SHA256见同名 JSON。未实现的命令不得报告通过。

3. **结果**：原四个提交直接发布为 `BLOCKED`。40个 requests/wires 文件（30个唯一内容 blob）保存完整 Provider body，包括 system prompt、历史问题和文档 Context；固定包清单/评分资料也含问题及来源身份。多份日志含本机用户目录，三份同内容 SSH stderr 暴露部署地址；结果 JSON 再次列出10个原问题。公开样本资料本身未被认定为私人内容，但本次扫描没有完整请求/原始 Context 的公开发布授权证据。已检查的新增/修改源码中未确认真实生产密钥；密钥形状命中为类型注解、空 Compose 环境注入、文档文字和测试 fixture。未联网验证密钥有效性。

4. **风险与遗留**：在原 HEAD 后删除文件仍会推送祖先提交的原始 blob。建议保留原本地分支，从远端基线创建清洁后继；选取源码差异并生成脱敏报告，用普通 fast-forward 更新同一远端任务分支。最小易复核的排除范围为全部159个新增 `docs/evaluation/evidence/s8_6_5/` 文件，原始证据继续私有保留。公开结果将 `/generation_comparison/per_case/*/question` 换为 SHA256；移除私有资源身份和绝对审计位置。公开报告须明确证据私有保留，不能制造公开可回读的假象。`tests/weknora_port/test_s865_generation_compare.py:16` 及多数测试依赖该私有包，需改为明确的合成 fixture；真实历史验收另保留私有入口。`scripts/s8_6_5_generation_compare.py:29`、`:33` 默认路径须明确要求私有包/配置，不得通过偷偷跳过测试解决。源码修改、提交、推送由主代理负责。

5. **版本与下一步**：原始 `7510b7a`、`9163f09`、`419f5d1`、`46be011` 保留；未 reset/amend/filter/force-push，未提交、推送或打 Tag。本报告提供逐路径排除/脱敏 manifest。主代理创建实际清洁后继后，须再扫描该后继相对远端新增的所有可达 blob，并检查公开测试与私有包解耦；本报告不提前声明后继已获发布通过。


清洁后继实施：分支 `codex/s8-6-5-r-publication-20261023` 从远端基线创建，原始分支和159份证据保留。本后继使用显式私有 package 路径与公开合成 fixture；定向 `python -m pytest tests/weknora_port/test_s865_generation_compare.py -q` 为19passed、exit0，仅证明离线契约。该结果不替代历史、真实 Provider 或正式 HTTP 验收。最终 staged blob 扫描结果另记于 `S8_6_5_R_SANITIZED_TREE_AUDIT.json`；尚未提交、推送或打 Tag。


最终清洁后继审查（2026-10-23）：剩余两处原始问题已替换为 SHA256，拟提交的 `progress.md` 中一处继承的实际部署地址也改为哈希。完整 staged/new-reachable blob 审查及源码逐文件映射见 `S8_6_5_R_SANITIZED_TREE_AUDIT.json` 与同名 Markdown；原四个提交仍因原始证据而不直接发布。本地提交已获本轮负责人授权，须以最终索引审计 PASS 为前提；推送由主代理协调，当前 `NOT RUN`，未打 Tag。历史公开祖先中的暴露无法由本后继撤回。
