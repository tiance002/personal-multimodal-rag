# ECS 诊断资源护栏与本轮恢复记录

记录日期：2026-10-23（Asia/Shanghai）

## 1. 目标与改动

本记录覆盖一次 ECS 重启后的服务恢复，以及供**离线诊断**使用的共享 systemd/cgroup 资源护栏。它不改变生产 API/Worker 的资源配置，不改变 P5、Prompt、模型、索引或检索策略，也不代表 S8.6.5 或 ECS 118 项语义验收完成。

恢复后实测主机约 3.5 GiB 内存、2 个 CPU 核、40 GiB 根盘、约 16 GiB 可用空间。PostgreSQL 物理身份及数据卷与重启前一致；WAL redo 完成并就绪。Redis AOF 加载结束且写入正常。Redis `run_id` 因进程重启变化，不能单独据此判断实例身份。

API/Worker 起初因旧 Redis pin 值导致无限重启。已核实仍为同一 Redis 容器、PostgreSQL 卷及业务数据后，备份运行时环境配置，只重建 API 与 Worker；两者沿用原 `80e2` 镜像。认证后的普通检查退出码为 0，4 个生产容器均运行且重启计数为 0。此为服务恢复证据，不是业务语义验收或新版本发布。

### 离线诊断护栏

root 已安装共享 `rag-diagnostics.slice` 和 `rag-diagnostic-run` 入口。护栏只适用于离线诊断作业，不适用于 Provider 批次或生产 API/Worker。所有作业共享 unit 内的 `flock`，SSH 断开后锁仍由 systemd 持有，避免并发诊断任务。

| 控制项 | 实际内核配置 / 行为 |
|---|---|
| CPU | `CPUQuota=50%`，内核读回 `50000/100000`，约半个核（本机 2 核的约 25%） |
| 内存 | `MemoryHigh=256 MiB`；`MemoryMax=384 MiB` |
| Swap / 进程数 | swap 配额 0；`TasksMax=64` |
| 块设备 I/O | 物理设备读 4 MiB/s、写 2 MiB/s；读 IOPS 128、写 IOPS 64 |
| 调度优先级 | `Nice=10`、idle I/O class、`OOMScoreAdjust=500` |
| 生命周期 | 不自动重启；control-group 清理；单次离线诊断最多 15 分钟，停止宽限 10 秒 |
| 互斥 | 共享锁在 unit 生命周期内持有，SSH 断连不释放 |

已核对运行中 unit 属性、实际 payload 所在 cgroup、`/proc` 的 `oom_score_adj=500`。`/usr/bin/true` 子进程退出码 0；该次 systemd 结果为 success，memory peak 为 7,917,568 字节。持有锁期间再运行一个 `true` 的受控冒烟按预期退出码 75。最初安装发现 I/O 数值后缀按十进制解释、`ExecStopPost` 中 `%n` 被当字面值两个小问题；均已修正为显式字节数和实际 unit 名称，并以实际运行验证。

## 2. 执行命令

已执行的目标主机命令（原 argv；不含 SSH 凭据）：

```text
bash install.sh
/opt/rag-s8/diagnostics/common/rag-diagnostic-run -- /usr/bin/true
/opt/rag-s8/diagnostics/common/rag-diagnostic-run -- /usr/bin/sleep 6
docker exec rag-s8-api-1 python -B -m scripts.s8_ecs_runtime check
```

最终安装与服务检查退出 0；受控 true 子进程退出 0；sleep 持锁期间第二个 true 在 unit 内拒绝执行，退出 75。启动器异步返回 0 只表示任务已提交，必须读取 unit 的最终收据，不能当作 payload 成功。CPU、内存、物理设备 I/O 从 cgroup v2 文件实际读取；运行中的 unit 属性及 payload 的 OOM 分数由 ECS Python 只读脚本核对，原始证据私有保留。

后续操作入口为上面的 `rag-diagnostic-run -- /absolute/executable ...`。不要用裸 `systemd-run` 或 `python` 启动重型诊断而绕开资源检查和共享锁。

## 3. 结果

- **恢复：PASS（真实 ECS）**。PG 数据身份/卷一致，WAL redo 完成；Redis AOF 加载完成且可写；仅 API/Worker 按原镜像恢复；普通认证检查 exit 0，4 个容器运行且无重启。
- **护栏：PASS（真实 ECS 内核读回与进程冒烟）**。CPU、内存、swap、任务数、物理设备 I/O、调度优先级及 OOM 分数配置已读回；成功与互斥拒绝路径均按预期退出。
- **业务验收：PENDING**。没有据此声称 S8.6.5、ECS 118 项或任何语义正确性验收完成；未发布生产代码，也未发起 Provider 调用。

原先报告采用无界完整载入，风险为 118 项与 173 项结果累积。新的离线派生报告有 7 项离线通过；该离线派生报告处理 100 道合成题与 100 张合成图片，每条额外载荷 2 MiB，完整 JSONL 输出合计 420,350,374 字节；Python allocation peak 记录为 8,339,801 字节。该数值不是 OS RSS，也不是 ECS 上测得的进程峰值；离线报告结果不得外推为 ECS 容量证明。

## 4. 风险与遗留

历史启动前内核日志没有保留此前启动周期的 OOM 条目。Owner 观察到 OOM，但具体进程根因**未验证**；本记录不把诊断作业认定为已证实的 OOM 根因。此护栏只限制通过指定 systemd unit 启动的离线作业，不提供整机 OOM 绝对保证，也不约束绕开入口启动的进程。

后续所有 SSH 工作仅执行轻量控制和状态读回；分析经共享包装器运行。源文件、报告及数据集审阅应串行进行；用元数据/路径引用代替整体加载约 387 MB JSONL；只保留一份不可变证据副本，不频繁重算完整哈希。禁止自动重试、自动提升资源上限或将此离线护栏用于 Provider 批次。磁盘 I/O 与跨任务吞吐未做压力测试，仍为 `NOT_RECORDED`。

## 5. 版本与下一步

护栏安装和冒烟已完成；生产应用镜像未变更，未做生产切换、模型变更或新版本发布。没有本次提交 SHA 或 tag。当前验收状态为 `PASS_GUARD_INSTALLED_REAL_SMOKE_BUSINESS_ACCEPTANCE_PENDING`。后续只在明确授权的任务中继续有限、串行的业务验收，并单独记录其语义结果；不得把本报告当作 S8.6.5 或 ECS 118 项完成证明。
