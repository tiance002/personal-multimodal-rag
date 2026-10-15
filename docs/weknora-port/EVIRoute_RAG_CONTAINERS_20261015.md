# EviRoute RAG 容器配置（2026-10-15）

## 目标状态

Compose 项目 slug 为 `eviroute-rag`，Docker Desktop 通常以这个 slug 分组显示；展示名称为 **EviRoute RAG**。Docker Compose 没有独立的显示名字段，项目名需要使用 slug。该 Compose 只管理本项目需要的 PostgreSQL 与 Redis：

| 服务 | 容器名 | 本地地址 | 数据卷 |
|---|---|---|---|
| PostgreSQL / ParadeDB | `eviroute-rag-postgres` | `127.0.0.1:56423` | 已有 external volume `weknora-python-resume-pg-20261013` |
| Redis | `eviroute-rag-redis` | `127.0.0.1:56424` | 已有 external volume `weknora-python-resume-redis-20261013` |

镜像固定为 `paradedb/paradedb:v0.22.6-pg17` 与 `redis:7.4.2`，并设置 `pull_policy: never`。PostgreSQL 继续使用已初始化数据库 `weknora_python_port_20261013` 和用户 `port_fixture`。Compose 不保存数据库密码、供应商密钥或其他凭据。

两个卷均标记为 external；卷不存在时 Compose 必须报错，不会创建替代空卷。启动辅助脚本还会在启动前单独检查两个卷。PostgreSQL 物理 `system_identifier` 已在迁移前后核对为 `7696088403504697382`。迁移前后消息数、UNKNOWN 任务数、tenant 任务数与文档数相同；Redis DB 15 的键数量前后均为 128，未执行 flush。本轮使用 Redis DB 15 下唯一测试前缀；该 DB 内其他前缀不属于本轮消费范围。

## 常用操作

在项目工作树根目录运行：

```powershell
.\scripts\eviroute-rag-dependencies.ps1 start
.\scripts\eviroute-rag-dependencies.ps1 status
.\scripts\eviroute-rag-dependencies.ps1 stop
```

`stop` 仅停止这两个 Compose 服务，不执行 `down`、容器删除或卷操作。Compose 定义本身仅覆盖 PostgreSQL 和 Redis；WeKnora 原容器与旧业务容器不属于本项目分组。当前 API 与 React 仍作为宿主进程运行，尚未容器化。

## 实际迁移结果

迁移已完成：Compose 项目 `eviroute-rag` 已创建 `eviroute-rag-postgres` 与 `eviroute-rag-redis`，两个容器均报告 `healthy`。PG/Redis 分别继续挂载原 external named volumes `weknora-python-resume-pg-20261013`、`weknora-python-resume-redis-20261013`，没有复制或删除卷。端口仍为 `127.0.0.1:56423` 与 `127.0.0.1:56424`。PG system identifier 及关键记录计数迁移前后相同；Redis DB 15 键数量前后相同且未 flush。

切换前已核对镜像 ImageID 与原本地镜像相同。验证旧 wrapper 后，原 PG 容器 `a5464dff5efb5e6c28811d06b9676dcd3c6910f1d5d63aaf872a619be635693d` 和 Redis 容器 `692b84d8fee031b145e6b1b0037625a955f0bb3cdb321854d11a6bbb03b02664` 已移除；移除命令未带 `-v`，两个外部命名卷保留。备份文件位于 `E:\WK\run\browser-accept-20261015\docker-migration\`：`approved-database.dump`、`redis-before.rdb`。迁移证据包含备份 SHA-256 与前后状态快照。

本次只迁移 PG/Redis 依赖容器；API 与 React 仍作为宿主进程运行，尚未容器化。此记录证明容器迁移和所列数据连续性检查，不代表整个应用已通过验收。

Owner 后续确认：前端、app/API 和解析部分暂不封装容器，继续以宿主机进程开发；当前分组仅包含 PostgreSQL 和 Redis。

## 验证记录

- `docker compose --project-name eviroute-rag --file docker-compose.eviroute-rag.yml config --quiet`：PASS，退出码 0。
- Compose 创建并启动两个服务：证据 `E:\WK\run\browser-accept-20261015\docker-migration\compose-up.txt`；PG 与 Redis 均 healthy。
- 前后状态快照：`before.json` 与 `after.json`；PG system identifier 与业务计数一致，Redis DB 15 大小均为 128。
- 旧 wrapper 移除记录：`remove-old-wrappers.txt`；移除未带 `-v`，外部命名卷保留。
