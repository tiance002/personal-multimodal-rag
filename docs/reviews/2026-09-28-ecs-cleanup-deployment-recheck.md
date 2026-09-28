# ECS 清理与部署授权复核

## 1. 目标与改动

用户再次批准按已列清单备份、清理并部署。读取附件、AGENTS、progress、已有部署代码和工作树状态后，发现该清单已于 2026-09-27 执行。本轮对实际服务器复核，不重复删除，不将尚未完成可信度修复的工作区代码发布。

旧资产备份位于 `/var/backups/study-plan/cleanup-20260927T143810Z/`。现有发布目录为 `/srv/rag-eval/releases/ad2fc3ec7d66dc591a95c9da22170c2f1f326d6376cf7a482f90a212420c7142`。凭据只用于运行时 SSH 参数，未写入仓库。

## 2. 执行命令

以下均通过严格主机密钥校验的 SSH 执行；连接参数包含运行时私钥，报告不保存其路径或内容。

| 远端命令 | 退出码 / 关键输出 |
|---|---|
| `sha256sum *.tar.gz *.dump`（在上述备份目录） | 0；资产 `1e427fc512525ba9669ae32c8f168b1c5511ea08d85e1b9e099aab3f99c9a12d`，dump `76cd48d52800f6a4d5fbea10cf3cbc83b5d088f80967dea6b0809b470d5a47ad`，均与原记录一致 |
| `tar -tzf study-plan-assets-20260927T143810Z.tar.gz >/dev/null` | 0，归档可读 |
| `pg_restore --exit-on-error --file=/dev/null study-platform-20260927T143810Z-pre-cleanup.dump` | 0，备份可解析；本轮未恢复数据库 |
| `test ! -e` 对 `/opt/study-plan`、`/etc/study-plan`、`/var/lib/study-plan`、`/var/www/letsencrypt` | 0，清单目录均已移除 |
| `systemctl list-unit-files --no-legend` 的 `study-plan-` 精确前缀检查；`systemctl is-active nginx` 状态断言 | 0，无旧项目 unit，Nginx inactive |
| `systemctl is-active rag-eval; systemctl is-enabled rag-eval` | 0，active / enabled |
| `readlink -f /srv/rag-eval/app; ss -lntp` | 0，发布目录如上，评测服务仅监听 127.0.0.1:8787，SSH 22 保持可用 |
| `curl --fail --silent http://127.0.0.1:8787/healthz` | 0，`{"status":"ok"}` |
| `curl --fail --silent http://127.0.0.1:8787/api/v1/experiments` | 0，两条已有实验仍在 |
| `free -m; df -h /` | 0，可用内存约3009 MiB，根盘35 GiB可用、使用率9% |

一次 PowerShell 数组传参 SSH 尝试退出255，未连接远端；改为显式调用系统 OpenSSH 后，上述复核成功。

## 3. 结果

**PASS：已授权旧环境备份、定向清理和首版部署的当前状态复核。** 这不是评测可信度整体验收；当前 P0/P1 修复 Goal 仍 active，先前审计 FAIL 未撤销。新代码部署、本轮真实 A/B 与全量重新验收仍 NOT RUN。未实现的命令不得报告通过。

## 4. 风险与遗留

备份在同一 ECS 根盘；异地恢复未验证。现有旧版实验属于先前六题 smoke，不能作为正式质量基准。PostgreSQL、旧数据库和证书材料保留。

## 5. 版本与下一步

本地工作树基线 `a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b`，修复未提交。既有 ECS 内容发布哈希如上，不能冒充新的 Git 提交版本。继续可信度修复、真实隔离 A/B、提交和匹配版本部署；无 Release Tag。
