import { useEffect, useState } from "react";
import type { RunTimeline, ProgressStage } from "../app/state";

const labels: Record<ProgressStage, string> = { run: "处理请求", understand: "理解问题", retrieval: "检索资料",
  evidence: "准备证据", generation: "生成回答", validation: "校验答案与引用" };
const statuses = { started: "进行中", completed: "已完成", failed: "未完成", truncated: "输出被截断", ended: "已结束" };
const endings = { pending: "正在处理", completed: "处理已完成", failed: "处理未完成", cancelled: "已停止等待", unknown: "运行状态暂无法确认" };

export function RunProgress({ timeline, onCancel }: { timeline: RunTimeline; onCancel?: () => void }) {
  const [expanded, setExpanded] = useState(timeline.status === "pending");
  useEffect(() => { if (timeline.status !== "pending") setExpanded(false); }, [timeline.status]);
  return <section className={`run-progress ${timeline.status}`} data-run-id={timeline.runId} aria-label="运行进度">
    <div className="run-progress-header">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
        <span className="run-progress-dot" />{endings[timeline.status]}<span className="run-progress-toggle">{expanded ? "收起" : "查看过程"}</span>
      </button>
      {timeline.status === "pending" && onCancel && <button type="button" className="run-progress-cancel" onClick={onCancel}>停止等待</button>}
    </div>
    {expanded && <ol className="run-progress-stages">
      {timeline.stages.length === 0 && <li>正在处理，等待阶段信息</li>}
      {timeline.stages.map(row => <li key={`${row.stage}-${row.attempt_id ?? ""}-${row.role ?? ""}`} className={`stage-${row.status}`}>
        <span className="run-stage-name">{labels[row.stage]}{row.stage === "generation" && row.role ? (row.role === "cheap" ? " · 轻量模型" : " · 高能力模型") : ""}</span>
        <span className="run-stage-status">{row.stage === "generation" && row.status === "started" ? "正在连接模型并生成回答" : statuses[row.status]}</span>
        {(row.candidate_count !== undefined || row.selected_evidence_count !== undefined || row.duration_ms !== undefined) &&
          <small>{[row.candidate_count !== undefined ? `候选片段 ${row.candidate_count}` : "",
            row.selected_evidence_count !== undefined ? `有效证据 ${row.selected_evidence_count}` : "",
            row.duration_ms !== undefined ? `耗时 ${(row.duration_ms / 1000).toFixed(1)} 秒` : ""].filter(Boolean).join(" · ")}</small>}
      </li>)}
    </ol>}
  </section>;
}
