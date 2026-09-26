import { useState } from "react";
import type { Message } from "../app/state";

type Props = { messages: Message[]; disabled: boolean; onSend: (content: string, mode: "quick" | "smart") => void; onCitation: (runId: string, citationId: string) => void };

export function ChatPanel({ messages, disabled, onSend, onCitation }: Props) {
  const [value, setValue] = useState("");
  const [mode, setMode] = useState<"quick" | "smart">("quick");
  const submit = () => { const content = value.trim(); if (!content) return; onSend(content, mode); setValue(""); };
  return <section className="chat-panel"><div className="chat-head"><div><div className="section-kicker">问答台</div><h2>把问题交给你的资料</h2></div><div className="mode-toggle" role="group" aria-label="回答模式"><button className={mode === "quick" ? "active" : ""} onClick={() => setMode("quick")}><span className="status-pip" />快速检索</button><button className={mode === "smart" ? "active" : ""} onClick={() => setMode("smart")}>智能推理</button></div></div><div className="message-stream">{messages.length === 0 ? <div className="chat-empty"><span className="big-star">✦</span><h3>从一个具体问题开始</h3><p>回答会保留真实版本、原文片段与定位。</p></div> : messages.map((message, index) => <article key={message.id ?? index} className={`message ${message.role}`}><div className="message-role">{message.role === "user" ? "你" : "索引台"}</div><div className="message-body">{message.content}{message.citations?.length ? <div className="citation-strip">{message.citations.map((citation) => <button key={citation} className="citation-pill" onClick={() => message.run_id && onCitation(message.run_id, citation)}>{citation}</button>)}</div> : null}</div></article>)}</div><div className="composer"><textarea aria-label="输入问题" disabled={disabled} value={value} onChange={(event) => setValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }} placeholder={disabled ? "先选择一个知识库" : "问一个关于当前资料的问题…"} /><button aria-label="发送问题" disabled={disabled || !value.trim()} onClick={submit}>↑</button><small>Enter 发送 · Shift + Enter 换行</small></div></section>;
}
