"""api/usage.go cache normalization with explicitly UNKNOWN absent usage."""
from collections.abc import Mapping
from .types import TokenUsage, ProviderError
_MISSING = object()


def normalize_usage(raw, cache_accounting: bool = False) -> TokenUsage:
    if raw is None:
        return TokenUsage(cache_status="unreported" if cache_accounting else "unsupported")
    if not isinstance(raw, Mapping):
        raise ProviderError("PROVIDER_USAGE_INVALID", sent=True)
    def count(name, value=_MISSING):
        n = raw.get(name) if value is _MISSING else value
        if n is not None and (type(n) is not int or (n < 0 and name in ("prompt_tokens", "completion_tokens", "total_tokens"))):
            raise ProviderError("PROVIDER_USAGE_INVALID", sent=True)
        return n
    prompt, completion, total = count("prompt_tokens"), count("completion_tokens"), count("total_tokens")
    if total in (None, 0) and prompt is not None and completion is not None:
        total = prompt + completion
    read = write = miss = 0
    reported = False
    details = raw.get("prompt_tokens_details") or {}
    if not isinstance(details, Mapping):
        raise ProviderError("PROVIDER_USAGE_INVALID", sent=True)
    if raw.get("prompt_cache_hit_tokens") is not None or raw.get("prompt_cache_miss_tokens") is not None:
        read, miss = count("prompt_cache_hit_tokens") or 0, count("prompt_cache_miss_tokens") or 0
        reported = True
    elif raw.get("cache_read_input_tokens") is not None or raw.get("cache_creation_input_tokens") is not None:
        read, write = count("cache_read_input_tokens") or 0, count("cache_creation_input_tokens") or 0
        miss, reported = max(0, (prompt or 0)-read), True
    elif details.get("cached_tokens") is not None or details.get("cache_write_tokens") is not None:
        read = count("cached_tokens", details.get("cached_tokens")) or 0
        write = count("cache_write_tokens", details.get("cache_write_tokens")) or 0
        miss, reported = max(0, (prompt or 0)-read), True
    elif raw.get("cached_tokens") is not None:
        read = count("cached_tokens") or 0
        miss, reported = max(0, (prompt or 0)-read), True
    read, write, miss = max(0,read), max(0,write), max(0,miss)
    cache_status = ("hit" if read > 0 else "miss") if reported else ("unreported" if cache_accounting else "unsupported")
    return TokenUsage(prompt, completion, total,
        "REPORTED" if prompt is not None and completion is not None and total is not None else "UNKNOWN",
        read, read, write, miss, reported, cache_status)


def accumulate_usage(usages: list[TokenUsage]) -> TokenUsage:
    if not usages:
        return TokenUsage()
    def total(name):
        values = [getattr(u, name) for u in usages]
        return None if any(v is None for v in values) else sum(values)
    read = sum(u.cache_read_tokens for u in usages)
    reported = any(u.cache_reported for u in usages)
    status = "hit" if reported and read else "miss" if reported else (
        "unsupported" if all(u.cache_status == "unsupported" for u in usages) else "unreported")
    return TokenUsage(total("prompt_tokens"), total("completion_tokens"), total("total_tokens"),
        "REPORTED" if all(u.status == "REPORTED" for u in usages) else "UNKNOWN", read, read,
        sum(u.cache_write_tokens for u in usages), sum(u.cache_miss_tokens for u in usages), reported, status)
