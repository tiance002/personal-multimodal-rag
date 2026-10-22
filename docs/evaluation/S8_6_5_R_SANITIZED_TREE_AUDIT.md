# S8.6.5-R 清洁发布树审查

1. **目标与改动**：从公开父提交 `db917c8a535b13b96ffd9ffbda18669025d5b3b3` 构建清洁后继，保留原本地 `7510b7a`、`9163f09`、`419f5d1`、`46be011`。排除159份原始证据，发布源码、脱敏结果、私有证据哈希清单与审查报告。最终又清理结果 JSON 两处完整历史问题、`progress.md` 一处继承的实际部署地址；原值仅以路径、行号、类别和 SHA256 记录。

2. **执行命令**：可信私有审查驱动 `complete_publication_audit.py --write-report` 与最终只读 `complete_publication_audit.py` 对 `git write-tree` 的拟提交树运行，成功退出码为0。它使用 `git ls-tree -rz`、`git cat-file --batch`、`git rev-list --objects db917c8a535b13b96ffd9ffbda18669025d5b3b3` 枚举新增可达 blob，并用静态 AST 比较核对源函数。`git diff --cached --check` 是提交前的必要检查。最终命令原文、退出码和树/commit 身份另保存在私有执行收据；机器结果详见同名 JSON。未实现的命令不得报告通过。

3. **结果**：最终 JSON 的 `status=PASS` 才表示本次静态敏感信息与源码身份审查通过。214个 backend/deploy 文件与 `9163f09` 的 Git blob 完全相同，后台 runner 与 `46be011` 完全相同；比较脚本只有 `prepare`、`dry_run`、`main` 的私有输入契约变化，实际执行函数不变。公开合成测试先前19passed、exit0，本轮脚本和测试内容未再修改，未重复运行。该测试只证明离线契约；真实 Provider/HTTP/PG/ECS 均 `NOT RUN`。

4. **风险与遗留**：扫描所有新增 blob 的完整内容，包括继承自基线的文字；循环地址、RFC5737 示例、测试脱敏字面量单独分类。检查10道固定原始问题、57段唯一文档 Context、完整请求结构及重叠片段；零阻断才通过。没有联网验证凭据，本报告不是全仓库安全审计。既有公开祖先不改写，祖先历史暴露不能由新提交撤回。审计 JSON/Markdown 也参与最终扫描，但不放入自身递归哈希 inventory；逐路径映射覆盖其他发布内容。

5. **版本与下一步**：分支 `codex/s8-6-5-r-publication-20261023`。本轮允许审计通过后建立本地提交，提交身份以 Git 与主代理汇总为准；推送和 Tag 尚未执行，主代理协调最终发布。本报告不宣称 S8.6.5 真实业务验收完成。
