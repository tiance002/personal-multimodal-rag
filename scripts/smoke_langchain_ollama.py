from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local Ollama LangChain tool calling without cloud fallback.")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--chat-model", default="qwen3.5:4b")
    parser.add_argument("--ollama-bin", default=r"E:\ollama\ollama.exe")
    parser.add_argument("--report", default=r"var\reports\smoke-langchain-ollama.json")
    args = parser.parse_args()

    report: dict[str, object] = {"model": args.chat_model, "base_url": args.base_url, "cloud_fallback": False}
    try:
        show = subprocess.run([args.ollama_bin, "show", args.chat_model], capture_output=True, text=True, check=False)
        report["ollama_show_exit_code"] = show.returncode
        report["ollama_capabilities"] = [line.strip() for line in show.stdout.splitlines() if line.strip() in {"tools", "thinking", "completion", "vision"}]
        if show.returncode != 0:
            raise RuntimeError("ollama show failed")

        @tool
        def probe_search(query: str) -> str:
            """Return one fixed, authorized probe evidence record."""
            return json.dumps({"chunk_id": "probe-1", "quote": "local tool evidence", "query_seen": query}, ensure_ascii=False)

        model = ChatOllama(model=args.chat_model, base_url=args.base_url, temperature=0, reasoning=False, seed=0, num_predict=512)
        agent = create_agent(
            model,
            [probe_search],
            system_prompt="Call probe_search exactly once before answering. Then answer briefly using its result.",
        )
        result = agent.invoke({"messages": [{"role": "user", "content": "What is the probe evidence?"}]})
        messages = result.get("messages", [])
        tool_calls = [call for message in messages for call in getattr(message, "tool_calls", []) if call.get("name") == "probe_search"]
        tool_messages = [message for message in messages if getattr(message, "type", "") == "tool"]
        final_messages = [message for message in messages if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", []) and str(getattr(message, "content", "")).strip()]
        valid_args = bool(tool_calls and isinstance(tool_calls[0].get("args", {}).get("query"), str) and tool_calls[0]["args"]["query"].strip())
        report.update({"message_count": len(messages), "tool_call_count": len(tool_calls), "tool_message_count": len(tool_messages), "final_message_count": len(final_messages), "tool_args_valid": valid_args})
        report["status"] = "PASS" if len(tool_calls) == 1 and len(tool_messages) == 1 and len(final_messages) == 1 and valid_args else "FAIL"
    except Exception as exc:
        report.update({"status": "FAIL", "error": type(exc).__name__})

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
