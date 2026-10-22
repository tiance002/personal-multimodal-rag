"""Small request-language policy for the S8.6.4 acceptance drivers."""
from __future__ import annotations

from collections.abc import Mapping

_LOCALES = {
    "en": "en-US", "en-us": "en-US", "en-gb": "en-GB",
    "zh": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN",
    "zh-tw": "zh-TW", "zh-hk": "zh-HK", "zh-hant": "zh-TW",
    "ja": "ja-JP", "ja-jp": "ja-JP", "ko": "ko-KR", "ko-kr": "ko-KR",
    "fr": "fr-FR", "fr-fr": "fr-FR", "de": "de-DE", "de-de": "de-DE",
    "es": "es-ES", "es-es": "es-ES", "pt": "pt-BR", "pt-br": "pt-BR",
    "ru": "ru-RU", "ru-ru": "ru-RU",
}
_ENGLISH_DIRECTIVES = (
    ("answer in english", "en-US"), ("reply in english", "en-US"),
    ("respond in english", "en-US"), ("answer in chinese", "zh-CN"),
    ("reply in chinese", "zh-CN"), ("respond in chinese", "zh-CN"),
    ("answer in french", "fr-FR"), ("reply in french", "fr-FR"),
    ("answer in japanese", "ja-JP"), ("answer in korean", "ko-KR"),
)
_CHINESE_DIRECTIVES = (
    ("请用简体中文回答", "zh-CN"), ("请用中文回答", "zh-CN"), ("用中文回答", "zh-CN"),
    ("请用繁体中文回答", "zh-TW"), ("请用英文回答", "en-US"), ("请用英语回答", "en-US"),
    ("用英文回答", "en-US"), ("用英语回答", "en-US"), ("请用日文回答", "ja-JP"),
    ("请用日语回答", "ja-JP"), ("请用韩文回答", "ko-KR"), ("请用韩语回答", "ko-KR"),
    ("请用法文回答", "fr-FR"), ("请用法语回答", "fr-FR"),
)


def _canonical_override(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    locale = _LOCALES.get(value.strip().replace("_", "-").casefold())
    if locale is None:
        raise ValueError("S864_UNSUPPORTED_EXPLICIT_LANGUAGE")
    return locale


def resolve_request_locale(q0: str, explicit_override: str | None = None) -> str:
    """Explicit supported override, then explicit q0 directive, then script default.

    The bounded acceptance profile supports listed BCP-47 locales. Without an
    explicit directive, any Han character selects zh-CN; otherwise English is
    the default. This is deliberately a script heuristic, not a language model.
    """
    override = _canonical_override(explicit_override)
    if override:
        return override
    folded = q0.casefold()
    for phrase, locale in _ENGLISH_DIRECTIVES + _CHINESE_DIRECTIVES:
        if phrase in folded:
            return locale
    has_han = any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"
                  or "\U00020000" <= char <= "\U0002fa1f" for char in q0)
    return "zh-CN" if has_han else "en-US"


def expected_profile_language(q0: str, explicit_override: str | None = None) -> tuple[str, str]:
    from backend.weknora_port.document_profile import language_name
    locale = resolve_request_locale(q0, explicit_override)
    return locale, language_name(locale)


def message_headers(base: Mapping[str, str], q0: str,
                    explicit_override: str | None = None) -> dict[str, str]:
    locale, _ = expected_profile_language(q0, explicit_override)
    headers = dict(base)
    headers["Accept-Language"] = locale
    if headers.get("Accept-Language") != locale:
        raise ValueError("S864_REQUEST_LANGUAGE_HEADER_MISMATCH")
    return headers


def policy_manifest() -> dict[str, object]:
    return {
        "version": "s8.6.4-request-language/v1",
        "precedence": ["explicit_answer_language", "explicit_q0_answer_directive", "q0_script_default"],
        "explicit_locale_tags": sorted(set(_LOCALES.values())),
        "default": {"contains_han": "zh-CN", "otherwise": "en-US"},
        "explicit_directive_support": "fixed common English/Chinese answer directives listed by implementation",
        "unsupported_explicit_locale": "S864_UNSUPPORTED_EXPLICIT_LANGUAGE",
    }
