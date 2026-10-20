# Darwin 持续进化合同

## 触发与顺序

用户部署目标优先。故障解决且实际验证后，自动归档事件；未解决先记PARTIAL/UNKNOWN，不为了写Skill拖延恢复。已有规则准确只新增必要案例，无需每次改SKILL或重复评分。

事件写入`incident-log.md`，字段：ID、阶段、现象、直接原因、根因/可信等级、失败尝试、有效修复、识别条件、预防、原始证据路径/字段/必要SHA。脱敏后保留命令错误原义，理论分支和合成测试不得冒充真实事故。

需要规则变化时先定位已安装`darwin-skill`并读实际规范。本次读取版本为v2.1/2026-06-10，SHA与本轮报告记录；不修改/升级全局Skill或其results.tsv。若未安装，读取官方指导或报告依赖，不能虚构执行结果。持久化进化记录放**本项目**`docs/deployment/ecs-skill/evolution/results.tsv`，不使用Darwin示例的全局`.claude`路径。

## 有限验证循环

1. 读取本项目`test-prompts.json`；默认复用3个已批准测试类别，新增测试说明与故障的联系，重要扩展进入Owner检查点。生成独立fixture和唯一输出目录，不访问ECS、不调用RAG Provider。
2. 先冻结当前已提交Skill、相关reference、Prompt/fixture与预期行为；未带Skill和带Skill用独立新上下文执行同一任务。`full_test`表示代理确实完成离线任务并保存输出，不表示真实ECS发布；主代理脑补只算`dry_run`。
3. 按Darwin9维rubric评估，绝对分仅作triage。评估者读取实际行为输出，不根据“写得长”给效果分；所有维度/权重及未评项如实记录。
4. 🔴 **CHECKPOINT**：展示基线、测试与单一改进提案；已有明确任务授权覆盖的有限编写/验证继续，新增权限、数据动作、重要部署规则改变或探索性重写先由Owner确认。不要把“分数低”自动变成扩权理由。
5. 每轮只改一个因素，候选与基线均保存Git版本/差异。3名独立judge在**同一次评估内**读取随机/交错顺序的两版及相同测试输出，依据9维标准返回better/worse/tie、clear/slight和理由；不知道哪版是作者偏好的版本。不用跨judge绝对分差决定保留。
6. 多数更差→拒绝/回退；没有明确行为收益或存在新风险→不推广，仅记录。最多3轮是上界，不是必跑数；达成目标、连续2轮slight/tie或两次无实质收益时收口，不凑改动。
7. 保留候选须同时满足行为测试与风险检查，并附Owner审查点。仅本地独立提交；回退使用本次独立优化提交的`git revert`，不reset、不覆盖dirty、不自动stash。存在冲突停该回退，报告保留的两版与差异，不能照Darwin通用示例覆盖文件。

## 留档与界限

每轮记录故障ID、旧/新Skill内容SHA和commit、diff、相同Prompt/fixture、独立输出、judge原结论、测试命令/exit、保留或拒绝理由。TSV沿实际Darwin9列：`timestamp commit skill old_score new_score status dimension note eval_mode`；paired的new_score填写投票数，分数不伪装为确定性测量。

主文件保持高频决策，不复制整份日志/脚本；修改超150%先精简。重要变化交Owner审查后再作为后续部署依据。调度器、后台监控和全局自改不在本Skill范围；“自动总结”由未来ECS任务触发规则执行，不承诺无人调用时运行。
