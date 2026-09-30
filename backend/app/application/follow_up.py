"""Bounded local referent context; previous answers never become evidence."""
import re


def resolve_follow_up(question: str, previous_question: str | None) -> tuple[str, bool]:
    if not previous_question or not re.search(r"^(?:那|它|他们|它们|这个|这种|这些|该|上述|其中|继续|(?:你)?刚才|你前面|前面)|\b(?:it|its|they|those|that method)\b", question, re.I):
        return question, False
    # Source retrieval and final citations still use the current server Scope.
    return f"上一轮问题背景（不是证据）：{previous_question[:1000]}\n只回答当前问题：{question}", True
