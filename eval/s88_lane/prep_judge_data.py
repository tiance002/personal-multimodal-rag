"""整理判分所需的输入：gold 答案、三组候选答案。

输出（都在本目录）：
- gold55.json        : [{ordinal, dataset, question, gold:[答案要点...]}]
- cand-qwen.jsonl    : Qwen 轮答案（有权威标签，用于校验判分器）
- cand-base.jsonl    : Xing + 原版 392 提示词
- cand-en.jsonl      : Xing + 英文 1257 提示词
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE.parent
PRIVATE = REPORTS.parent / "s8-8-r2-20261024" / "private"


def load_gold() -> list[dict]:
    """把 gold 答案要点抽出来，字段压到最小。"""
    src = json.loads((PRIVATE / "ECS55_R2_FULL_QA_CONTEXT.private.json").read_text(encoding="utf-8"))
    out = []
    for case in src["cases"]:
        gold = case.get("original_gold") or {}
        # 两套数据集口径不同：longbench-zh 用 answers 列表，hotpotqa / mmlongbench 用单个 answer
        answers = gold.get("answers")
        if answers is None:
            answers = gold.get("answer")
        if answers is None:
            answers = []
        if isinstance(answers, str):
            answers = [answers]
        if not isinstance(answers, list):
            answers = [str(answers)]
        out.append({
            "ordinal": case["ordinal"],
            "dataset": case.get("dataset"),
            "question": case.get("question"),
            "gold": [str(a).strip() for a in answers if str(a).strip()],
        })
    return apply_fixups(sorted(out, key=lambda c: c["ordinal"]))


def apply_fixups(rows: list[dict]) -> list[dict]:
    """叠加人工评审过的 gold 修订。

    只改被判为「标注有歧义」的题：原始 gold 保留在 gold_original，
    新 gold 是评审认定的正确表述，also_accept 是同样可接受的等价答案。
    修订记录写在 gold-fixups.json，逐条带理由，便于回退与复核。
    """
    path = HERE / "gold-fixups.json"
    if not path.exists():
        return rows
    fixups = {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    for row in rows:
        fix = fixups.get(row["ordinal"])
        if not fix:
            continue
        row["gold_original"] = row["gold"]
        row["gold"] = fix["gold"]
        row["also_accept"] = fix.get("also_accept") or []
        row["gold_note"] = fix.get("note", "")
    return rows


def load_qwen() -> list[dict]:
    """Qwen 轮的答案（A 组），只有完成题。"""
    cases = json.loads((REPORTS / "a-group.json").read_text(encoding="utf-8"))
    out = []
    for case in cases:
        answer = case.get("answer")
        if not case.get("is_completed") or not answer:
            continue
        out.append({"ordinal": case["ordinal"], "answer": answer})
    return sorted(out, key=lambda c: c["ordinal"])


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
        newline="",
    )


def main() -> None:
    gold = load_gold()
    (HERE / "gold55.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=1), encoding="utf-8", newline=""
    )
    print(f"gold55.json: {len(gold)} 题")

    qwen = load_qwen()
    write_jsonl(HERE / "cand-qwen.jsonl", qwen)
    print(f"cand-qwen.jsonl: {len(qwen)} 题")

    for name in ("base", "en"):
        rows = [json.loads(line) for line in (HERE / f"answers-{name}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        slim = [{"ordinal": r["ordinal"], "answer": r.get("answer") or ""} for r in rows]
        write_jsonl(HERE / f"cand-{name}.jsonl", slim)
        print(f"cand-{name}.jsonl: {len(slim)} 题")

    # 体积自检：判分数据要小到能直接塞进容器
    total = sum(p.stat().st_size for p in HERE.glob("*.json*"))
    print(f"本目录合计 {total/1024:.0f} KB")


if __name__ == "__main__":
    main()
