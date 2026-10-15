"""Immutable output-budget contract.

Separates, once and immutably, the business answer target from the final wire
generation cap, the cap's metering semantics, whether reasoning is inside that
cap, and the metered output upper bound actually reserved for an approved call.

The provider's declared completion-cap field is recomputed from the code-owned
pinned registry and cross-checked against this module's cap-semantics table, so
the two cannot drift. No fixed, empirical, percentile or per-call reasoning
allowance is ever invented here: a provider/API combination without a stated,
verifiable output-cap contract is a case-D refusal.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import catalog
from .types import ModelConfig, ProviderError

# Cap semantics.
#   full-completion/v1    the declared field bounds the whole completion
#                         (reasoning included): output_upper is that single cap.
#   visible+reasoning/v1  the vendor bounds visible output and reasoning as two
#                         separate hard caps: output_upper is their sum and it is
#                         admissible only when both caps are present on the wire.
#   no-generation/v1      the capability produces no completion: upper is 0.
FULL_COMPLETION = "full-completion/v1"
VISIBLE_PLUS_REASONING = "visible+reasoning/v1"
NO_GENERATION = "no-generation/v1"

CAP_FIELD_DRIFT = "MODEL_OUTPUT_CAP_FIELD_DRIFT"
CONTRACT_UNKNOWN = "MODEL_OUTPUT_CONTRACT_UNKNOWN"
CONTRACT_INVALID = "MODEL_OUTPUT_CONTRACT_INVALID"
HARD_LIMIT_DENIED = "MODEL_OUTPUT_HARD_LIMIT_DENIED"

CONTRACT_VERSION = "provider-completion-cap/v2"

_COMPAT_NAME = {"openai-completions": "OpenAICompletions",
                "openai-responses": "OpenAIResponses",
                "anthropic-messages": "AnthropicMessages",
                "google-generative-ai": "GoogleGenerativeAI"}

# Code-owned provider cap contracts, keyed by (provider, api):
# (declared completion-cap field, cap semantics, separate reasoning field or None).
# A contract is admissible only when its declared field equals the pinned
# registry's vendor override for the same provider/protocol.
CAP_CONTRACTS = {
    ("deepseek", "openai-completions"): ("max_tokens", FULL_COMPLETION, None),
    ("siliconflow", "openai-completions"): ("max_tokens", FULL_COMPLETION, None),
}


def registry_cap_field(provider, api):
    """The pinned registry's declared completion-cap field for provider/api, or None."""
    name = _COMPAT_NAME.get(api)
    if not name:
        return None
    vendor = catalog.provider_definition(provider)
    return (vendor.get("Compat", {}).get(name) or {}).get("MaxTokensField")


def _thinking_state(config, body):
    """Read the effective thinking mode/effort from the wire; never inferred."""
    mode, effort = "unspecified", str(body.get("reasoning_effort") or "")
    thinking = body.get("thinking")
    if config.chat.thinking_format == "thinking-type" and isinstance(thinking, dict):
        mode = {"enabled": "enabled", "disabled": "disabled"}.get(thinking.get("type"), "unspecified")
    elif config.chat.thinking_format == "enable-thinking":
        value = body.get("enable_thinking")
        mode = "enabled" if value is True else "disabled" if value is False else "unspecified"
    elif config.chat.thinking_format == "openai" and body.get("reasoning_effort"):
        mode = "specified"
    return mode, effort


@dataclass(frozen=True)
class OutputBudgetContract:
    """One immutable, recomputable output-budget contract for a single call."""

    provider: str
    api: str
    capability: str
    cap_field: str          # final wire generation-cap field ('' when non-generative)
    cap_value: int          # final wire generation cap (0 when non-generative)
    cap_semantics: str
    reasoning_field: str    # separate reasoning hard-cap field ('' when none)
    reasoning_upper: int
    thinking_mode: str
    reasoning_effort: str
    reasoning_included: bool
    output_upper: int       # metered output reservation upper bound
    upper_source: str
    case: str               # 'A' | 'B' | 'C' | 'D'


def resolve_output_contract(config, body, *, capability="chat"):
    """Recompute the output-budget contract from the resolved config and final wire.

    The cap field/value are read from the wire and required to equal the
    registry-declared field for this provider/API. Nothing here widens the
    caller's generation cap or invents a reasoning allowance.
    """
    if not isinstance(config, ModelConfig) or not isinstance(body, dict):
        raise ProviderError(CONTRACT_INVALID)
    mode, effort = _thinking_state(config, body)
    if capability not in ("chat", "vision"):
        # Embedding/rerank return no completion; the metered output upper is 0.
        return OutputBudgetContract(config.provider, config.api, capability, "", 0,
            NO_GENERATION, "", 0, mode, effort, False, 0, "non-generative-capability/v1", "A")
    entry = CAP_CONTRACTS.get((config.provider, config.api))
    if entry is None:
        # Case D: no verifiable hard output upper bound for this combination.
        raise ProviderError(CONTRACT_UNKNOWN)
    field, semantics, reasoning_field = entry
    declared = registry_cap_field(config.provider, config.api)
    if declared is not None and declared != field:
        raise ProviderError(CAP_FIELD_DRIFT)
    value = body.get(field)
    if type(value) is not int or value <= 0:
        raise ProviderError(HARD_LIMIT_DENIED)
    reasoning_upper = 0
    if semantics == FULL_COMPLETION:
        # Reasoning is inside the same cap: never add a second allowance.
        output_upper, case = value, "A"
    elif semantics == VISIBLE_PLUS_REASONING:
        if not reasoning_field:
            raise ProviderError(CONTRACT_UNKNOWN)
        separate = body.get(reasoning_field)
        if type(separate) is not int or separate <= 0:
            raise ProviderError(HARD_LIMIT_DENIED)
        output_upper, reasoning_upper, case = value + separate, separate, "B"
    else:
        # Case C/D: an unstated semantics is not a verified total upper bound.
        raise ProviderError(CONTRACT_UNKNOWN)
    return OutputBudgetContract(config.provider, config.api, capability, field, value,
        semantics, reasoning_field, reasoning_upper, mode, effort, True, output_upper,
        CONTRACT_VERSION, case)
