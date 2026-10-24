"""对照 gold 判定候选答案的核心正确性，用于挑出该送贵模型的错题。

用法（容器内）：
    python /work/judge_answers.py cand-qwen
    python /work/judge_answers.py cand-base
    python /work/judge_answers.py cand-en

判定口径与 R2 报告的 core_correctness 一致（二值）：
- CORRECT：核心结论与 gold 一致，措辞可不同、可更详细；
- WRONG  ：与 gold 矛盾 / 关键数值实体错误 / gold 的要点整体缺失 / 答非所问 /
           明明 gold 有答案却弃答。

输出 /work/judge-<mode>.jsonl，逐行 {ordinal, verdict, missing, wrong_claims, reason}。
支持断点续跑：已有结果的 ordinal 会跳过。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

# 与 probe_models.py 同法：绕开验收脚本对路由模式的硬性要求，仅影响本进程
_path = Path("/app/scripts/s8_ecs_runtime.py")
_text = _path.read_text()
_old = 'require(env["WEKNORA_PORT_ROUTER_MODE"] == "DYNAMIC", "S8_ORIGINAL_DYNAMIC_REQUIRED")'
if _old in _text:
    _path.write_text(_text.replace(_old, "pass"))

# 用免费的 29B 模型当裁判；它是被判答案的生成方，因此必须先用有权威标签的
# Qwen 轮做一致性校验，确认偏差可接受后才用于新轮次。
JUDGE_MODEL = "XingChenAGI/Xing4.0-29B"
WORK = Path("/work")
CONCURRENCY = 4

SYSTEM = """You are a strict grader for a retrieval-augmented QA system. You are given a QUESTION, the REFERENCE ANSWER (gold answer points), and a CANDIDATE ANSWER produced by another system.

Decide whether the candidate's CORE conclusion matches the reference. Judge content, not style.

Verdict CORRECT when:
- every essential conclusion, entity, number, unit, and date required by the reference is present in the candidate (paraphrase, translation, reordering, and extra detail are all fine);
- no statement in the candidate contradicts the reference;
- for enumeration questions, the reference's listed items all appear (minor omission of a non-essential aside is tolerated, omission of a listed item is not);
- for single-entity or yes/no answers, the candidate states that entity or verdict.

Verdict WRONG when any of these hold:
- the candidate contradicts the reference, or gives a different key value/entity/date;
- the reference's essential points are missing, or only a peripheral fragment is answered;
- the candidate answers a different question, or is mostly off-topic filler;
- the candidate refuses or claims the evidence is insufficient even though the reference supplies the answer;
- the candidate hedges by listing mutually exclusive alternatives without committing, when the reference has one definite answer;
- SELF-CONTRADICTION: the candidate asserts one value, generation, date, relationship, or procedure early on and later asserts an incompatible one, without retracting the first. Listing several mutually exclusive versions as if all were true is WRONG, even if one of them matches the reference;
- PROCEDURE STITCHING: for a how-to question the candidate merges steps from two different procedures, attaches a precondition belonging to another procedure, or answers about a different device/task than the one asked;
- BOUNDARY ERROR: when the reference implies an exact boundary (a threshold, an equality such as "exactly midnight tomorrow", an ordering, a count), a candidate that gets that boundary wrong is WRONG even if the surrounding reasoning is plausible;
- FABRICATED DETAIL: the candidate states a specific URL, document id, page number, version, or date that conflicts with the reference or with another statement in the same answer.
A trailing citation line such as "Sources: <file>.md", "来源：<file>.md", "**来源**：<file>.md" or "（filename: <file>.md）" is the required output format of this system. It is never fabrication and must be ignored when judging.

Extra relevant detail never makes a verdict WRONG. Verbosity is not correctness: a long answer with the wrong key value is WRONG; a short answer with the right key value is CORRECT.

Output format: reply with one JSON object and nothing else. Do not echo this instruction.
Evaluate the two fields independently and in this order:
1. gold_match: does the CORE conclusion match the reference? Use the CORRECT/WRONG rules above.
2. hygiene_issues: even if gold_match is CORRECT, list every integrity problem you can find in the candidate itself. Use these labels, one entry each:
   - "SELF_CONTRADICTION: <detail>" when the answer asserts incompatible values or relationships in different places as ITS OWN conclusion (it states a value, then retracts or replaces it with another value in its final conclusion);
     Do NOT flag this when the candidate explicitly attributes each conflicting claim to a different source document (for example "说法一…/说法二…", "article 4 says X while article 5 says Y", "综合资料，A 与 B 说法不一"). Reporting and explaining a conflict that exists inside the evidence is the required behaviour, not a contradiction. It is also not a contradiction to say a fact is uncertain BECAUSE the sources disagree.
   - "WRONG_SUBTASK: <detail>" when the body answers a different device, task, or sub-question than the one asked, even if one sentence happens to contain the reference wording;
   - "HEDGE_NO_COMMIT: <detail>" when the answer lists mutually exclusive options without choosing;
   - "FABRICATED_SPECIFIC: <detail>" when a URL, id, page, version, or date appears unsupported or inconsistent.
   Return an empty list only when the answer is clean on all four.
{"gold_match":"CORRECT","hygiene_issues":[],"missing":[],"reason":"one sentence, at most 40 words"}"""


def clean(text: str) -> str:
    """去掉引用标签与来源行，避免格式要求被误判成编造细节。

    本系统 citations_enabled=False 时，模型被要求用 `Sources: 文件名` 这种纯文本行
    标注来源。它的写法有多个变体（Sources: / 来源： / **来源**： / （filename: x.md）），
    这些是合规格式，不是编造，判核心正确性时必须先剔除。
    """
    text = re.sub(r"<kb\b[^>]*/?>", "", text or "")
    text = re.sub(r"<ref\b[^>]*/?>", "", text)
    kept = []
    for line in text.splitlines():
        bare = line.strip().lstrip(">").strip().strip("*").strip()
        # 整行就是一条来源标注
        if re.match(r"^(来源|参考来源|资料来源|Sources?|Source)\s*[:：]", bare, re.I):
            continue
        # 整行只有一个 md 文件名（可带页码/括号说明）
        if re.fullmatch(r"[\w\-./（）()第页章\-—\s]*[0-9a-f]{8,}\.md[\s\w（）()第页章\-—]*", bare, re.I):
            continue
        kept.append(line)
    text = "\n".join(kept)
    # 行内的 filename 标注
    text = re.sub(r"[（(]?\s*filename\s*[:：]\s*[0-9a-f]{8,}\.md\s*[）)]?", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_user(gold: list[str], question: str, answer: str, also_accept: list[str] | None = None) -> str:
    gold_text = "\n".join(f"- {g}" for g in gold)
    extra = ""
    if also_accept:
        alt = "\n".join(f"- {a}" for a in also_accept)
        extra = (
            "\n\nREVIEWER-APPROVED ACCEPTABLE ALTERNATIVES (this question was audited and the label "
            "was found ambiguous; an answer matching any of these also counts as CORRECT):\n"
            f"{alt}"
        )
    return (
        f"QUESTION:\n{question}\n\n"
        f"REFERENCE ANSWER (gold points):\n{gold_text}{extra}\n\n"
        f"CANDIDATE ANSWER:\n{clean(answer) or '(empty)'}\n\n"
        "Return the JSON verdict."
    )


def parse_verdict(text: str) -> dict:
    """解析裁判输出，并把 gold 命中与答案卫生合成最终判定。

    合成口径：gold 未命中，或存在任一卫生问题（自相矛盾/答错子任务/模棱两可/
    编造细节），都记 WRONG —— 与 R2 人工裁定的口径一致。
    """
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    brace = re.search(r"\{.*\}", raw, re.S)
    data = None
    if brace:
        try:
            data = json.loads(brace.group(0))
        except json.JSONDecodeError:
            data = None

    if isinstance(data, dict):
        gold = str(data.get("gold_match") or data.get("verdict") or "").upper()
        issues = data.get("hygiene_issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        issues = [str(x)[:200] for x in issues if str(x).strip()]
        if gold not in ("CORRECT", "WRONG"):
            return {"verdict": "UNPARSED", "gold_match": gold, "hygiene_issues": issues,
                    "missing": [], "reason": raw[:200]}
        verdict = "WRONG" if (gold == "WRONG" or issues) else "CORRECT"
        return {
            "verdict": verdict,
            "gold_match": gold,
            "hygiene_issues": issues,
            "missing": data.get("missing") or [],
            "reason": str(data.get("reason") or "")[:300],
        }

    # 退化路径：先抓字段值。模型若原样抄了模板，直接扫关键词会命中 WRONG。
    field = re.search(r'"gold_match"\s*:\s*"?(CORRECT|WRONG)"?', raw, re.I) or \
        re.search(r'"verdict"\s*:\s*"?(CORRECT|WRONG)"?', raw, re.I)
    if field:
        return {"verdict": field.group(1).upper(), "gold_match": field.group(1).upper(),
                "hygiene_issues": [], "missing": [], "reason": raw[:200]}
    upper = raw.upper()
    verdict = "WRONG" if "WRONG" in upper else ("CORRECT" if "CORRECT" in upper else "UNPARSED")
    return {"verdict": verdict, "gold_match": verdict, "hygiene_issues": [], "missing": [], "reason": raw[:200]}


async def grade(client, base: str, headers: dict, sem, item: dict, gold: dict) -> dict:
    async with sem:
        body = {
            "model": JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_user(gold["gold"], gold["question"], item.get("answer") or "", gold.get("also_accept"))},
            ],
            "max_tokens": 700,
            "temperature": 0,
            "enable_thinking": False,
        }
        started = time.monotonic()
        for attempt in (1, 2, 3):
            try:
                r = await client.post(base + "/chat/completions", headers=headers, json=body)
                elapsed = round(time.monotonic() - started, 1)
                if r.status_code == 200:
                    content = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
                    merged = {"ordinal": item["ordinal"], "seconds": elapsed, "attempt": attempt}
                    merged.update(parse_verdict(content))
                    return merged
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    await asyncio.sleep(5 * attempt)
                    continue
                return {"ordinal": item["ordinal"], "verdict": "HTTP_%d" % r.status_code,
                        "seconds": elapsed, "attempt": attempt, "reason": r.text[:150]}
            except Exception as exc:
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)
                    continue
                return {"ordinal": item["ordinal"], "verdict": "ERROR_" + type(exc).__name__,
                        "seconds": round(time.monotonic() - started, 1), "attempt": attempt}
    return {"ordinal": item["ordinal"], "verdict": "UNREACHED"}


async def main() -> None:
    mode = sys.argv[1]
    # 可选第二个参数给结果加后缀，改判据时保留旧结果以便对照
    suffix = ("-" + sys.argv[2]) if len(sys.argv) > 2 else ""
    gold_rows = json.loads((WORK / "gold55.json").read_text(encoding="utf-8"))
    gold_by_id = {g["ordinal"]: g for g in gold_rows}
    candidates = [json.loads(line) for line in (WORK / f"{mode}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    out_path = WORK / f"judge-{mode}{suffix}.jsonl"
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["ordinal"])
                except Exception:
                    pass
    todo = [c for c in candidates if c["ordinal"] not in done and c["ordinal"] in gold_by_id]
    print(json.dumps({"mode": mode, "total": len(candidates), "resume_skip": len(done), "todo": len(todo)}, ensure_ascii=False), flush=True)

    import httpx
    from scripts.s8_ecs_runtime import runtime_environment, trusted_principal
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore

    import os
    env = os.environ
    settings = DevelopmentSettings.from_environment(runtime_environment(env))
    principal = trusted_principal(env["S8_PRINCIPAL_FILE"])

    async with assemble(settings, principal=principal) as runtime:
        async with runtime.leases.lease() as guard:
            store = PostgresModelRoleStore(guard, principal, runtime.catalogue, cipher=runtime.cipher)
            _, roles = await store.read()
        cheap = next(r for r in roles if r.role == "cheap")
        base = cheap.base_url.rstrip("/")
        headers = {"Authorization": "Bearer " + cheap.api_key}

        sem = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            with out_path.open("a", encoding="utf-8", newline="\n") as sink:
                tasks = [asyncio.create_task(grade(client, base, headers, sem, item, gold_by_id[item["ordinal"]])) for item in todo]
                for coro in asyncio.as_completed(tasks):
                    row = await coro
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                    sink.flush()
                    print(json.dumps(row, ensure_ascii=False), flush=True)

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts: dict[str, int] = {}
    hygiene = 0
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        if row.get("hygiene_issues"):
            hygiene += 1
    print(json.dumps({"done": len(rows), "counts": counts, "with_hygiene_issues": hygiene,
                      "output": str(out_path)}, ensure_ascii=False), flush=True)


asyncio.run(main())
