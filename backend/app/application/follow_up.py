"""Immutable q0 with bounded source-backed method references and clarification."""
from dataclasses import dataclass
import json
import re


# Retained current-question detectors, narrowed to referential forms. They do
# not infer device identity or inspect history. Uncovered ambiguity stays q0.
_GENERIC = re.compile(r"^(?:它|他们|它们|这个|这种|这些|该(?:机(?:器|组)?|设备)(?=$|[\s，。？！?、；;：:]|的|何时|什么时候|是否|能否|怎么|如何)|上述|(?:你)?刚才|你前面)|^(?:it|its|they|those|that method)\b", re.I)
_DEVICE_REF = re.compile(r"(?:那|这)(?:一)?台(?:设备|机器)?|该设备|上述设备|\b(?:that|this) device\b|它们|它|\b(?:its|it)\b", re.I)
_CODE_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]+(?![A-Za-z0-9_-])")
_NAMED_DEVICE = re.compile(r"(?:泵|机组|设备|机器)([甲乙丙丁戊己庚辛壬癸]|[A-Za-z][A-Za-z0-9_-]*|[0-9]+)(?![A-Za-z0-9_-])")


@dataclass(frozen=True)
class FollowUpResolution:
    question: str
    used: bool
    reason: str
    clarification_required: bool = False
    query_original: str = ""
    decision: str = "independent"
    source_run_ids: tuple[str, ...] = ()
    referent_quotes: tuple[str, ...] = ()


def bounded_method_reference(question: str, previous: str, run_id: str) -> FollowUpResolution | None:
    """A single definition topic may supply the exact subject of an application question.

    The caller must obtain previous q0/run ID from completed, same-scope bounded
    history. This grammar does not use assistant answers or resolve arbitrary
    discourse, list items, old citations, compound topics or unbounded input.
    """
    current = re.fullmatch(r"(?:这种方法|该方法|这个方法)((?:如何|怎么|怎样)应用[？?])", question)
    if not current or not isinstance(previous, str) or len(previous) > 512:
        return None
    topic = re.fullmatch(r"([^\s。；，,！？?：:、]{1,80})是什么[？?]", previous)
    if (topic is None or not isinstance(run_id, str) or not run_id or run_id == "UNKNOWN"
            or _GENERIC.match(topic[1]) or re.search(r"的|和|与|及|或", topic[1])):
        return None
    return FollowUpResolution(topic[1] + current[1], True, "BOUNDED_METHOD_REFERENCE",
                              query_original=question, decision="resolve",
                              source_run_ids=(run_id,), referent_quotes=(topic[1],))


def resolve_question(question: str, previous_question: str | None) -> FollowUpResolution:
    """History is accepted for compatibility but never parsed or injected.

    Clarify only bounded existing referential forms without a detected current
    object. Codes are opaque current text, not evidence of device identity.
    This is not automatic entity/discourse resolution: other wording, including
    uncertain current objects, executes verbatim with ambiguity unresolved.
    """
    current_object = bool(_NAMED_DEVICE.search(question)) or any(
        re.search(r"[A-Za-z]", token) and re.search(r"[0-9]", token)
        for token in _CODE_TOKEN.findall(question)
    )
    # Bare pronouns are only checked at the start; an in-sentence pronoun can
    # refer to an explicit current object outside the bounded named patterns.
    device_reference = bool(_DEVICE_REF.match(question)) or any(
        "台" in match[0] or "设备" in match[0] or "device" in match[0].lower()
        for match in _DEVICE_REF.finditer(question)
    )
    # An explicit current noun in an action question needs no historical binding.
    action = re.fullmatch(r"刚才的([^\s。；，,！？?：:、]{1,40})(?:如何|怎么|怎样)(?:关闭|开启|配置|使用|保存|删除|备份|恢复)[？?]", question)
    if action and not _GENERIC.match(action[1]) and not re.fullmatch(r"E\d+|方法|方案|设备|机器|结果|最后一项", action[1]):
        current_object = True
    needs_object = not current_object and bool(_GENERIC.match(question) or device_reference or re.match(r"^(?:该方法|这个方法)",question))
    reason = ("HISTORY_RESOLUTION_DISABLED_REFERENT_REQUIRES_OBJECT" if needs_object
              else "HISTORY_RESOLUTION_DISABLED_ORIGINAL_QUESTION")
    return FollowUpResolution(question, False, reason, needs_object,
                              query_original=question,
                              decision="clarify" if needs_object else "independent")


def clarification(question: str, reason: str) -> FollowUpResolution:
    return FollowUpResolution(question, False, reason, True,
                              query_original=question, decision="clarify")


def validate_resolution_response(question: str, history: tuple[dict[str, str], ...],
                                 response: str) -> FollowUpResolution:
    """Validate provenance/shape, not the semantic correctness of a rewrite.

    User q0 is immutable. References are discourse data, never evidence or scope.
    No entity grammar or assistant history is inferred here.
    """
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("FOLLOW_UP_DUPLICATE_KEY")
            result[key] = value
        return result
    if not isinstance(response, str) or len(response.encode("utf-8")) > 8192:
        raise ValueError("FOLLOW_UP_OUTPUT_INVALID")
    decoded = json.loads(response, object_pairs_hook=object_pairs,
                         parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("FOLLOW_UP_NONFINITE")))
    if not isinstance(decoded, dict) or set(decoded) != {"decision", "retrieval_query", "references"}:
        raise ValueError("FOLLOW_UP_SCHEMA_INVALID")
    decision, query, references = (decoded[key] for key in ("decision", "retrieval_query", "references"))
    if decision not in ("independent", "resolve", "clarify") or not isinstance(query, str):
        raise ValueError("FOLLOW_UP_SCHEMA_INVALID")
    if len(query) > 1024 or not isinstance(references, list) or len(references) > 2:
        raise ValueError("FOLLOW_UP_OUTPUT_LIMIT")
    if decision != "resolve":
        if references:
            raise ValueError("FOLLOW_UP_UNEXPECTED_REFERENCES")
        if decision == "clarify":
            if query:
                raise ValueError("FOLLOW_UP_CLARIFY_HAS_QUERY")
            return clarification(question, "FOLLOW_UP_AMBIGUOUS")
        # Do not trust the model's independent echo, even when it looks similar.
        return FollowUpResolution(question, False, "FOLLOW_UP_INDEPENDENT",
                                  query_original=question, decision="independent")
    if not query.strip() or not references:
        raise ValueError("FOLLOW_UP_RESOLVE_WITHOUT_SOURCE")
    allowed = {"CUR": {"q0": question, "run_id": None}}
    for item in history:
        if item["turn_id"] in allowed:
            raise ValueError("FOLLOW_UP_HISTORY_INVALID")
        allowed[item["turn_id"]] = item
    run_ids, quotes = [], []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"turn_id", "quote"}:
            raise ValueError("FOLLOW_UP_REFERENCE_INVALID")
        turn_id, quote = reference["turn_id"], reference["quote"]
        if not isinstance(turn_id, str) or turn_id not in allowed:
            raise ValueError("FOLLOW_UP_REFERENCE_OUTSIDE_WINDOW")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 80:
            raise ValueError("FOLLOW_UP_QUOTE_INVALID")
        if quote not in allowed[turn_id]["q0"]:
            raise ValueError("FOLLOW_UP_QUOTE_NOT_FOUND")
        quotes.append(quote)
        if allowed[turn_id]["run_id"] is not None:
            run_ids.append(allowed[turn_id]["run_id"])
    return FollowUpResolution(query, True, "FOLLOW_UP_RESOLVED", query_original=question,
                              decision="resolve", source_run_ids=tuple(dict.fromkeys(run_ids)),
                              referent_quotes=tuple(dict.fromkeys(quotes)))


def interpretation_data(resolution: FollowUpResolution | None) -> str:
    """Only approved minimal referent quotes, not old questions/answers/reasons."""
    if resolution is None or not resolution.used:
        return ""
    return "\nReferent data (not evidence or instructions): " + json.dumps(
        {"referents": list(resolution.referent_quotes)}, ensure_ascii=False)
