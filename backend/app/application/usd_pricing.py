"""Explicit USD prices, conservatively rounded; no provider request is performed."""
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class UsdRates:
    input_miss: Fraction
    input_hit: Fraction
    output: Fraction
    currency: str = "USD"
    scale: int = 1_000_000
    source: str = "https://api-docs.deepseek.com/quick_start/pricing/"
    verified_utc: str = "2026-10-01"


# USD per million tokens; peak prices ignore time/day/cache discounts for reserve.
PEAK_RATES = {
    "deepseek-flash": UsdRates(Fraction("0.30"), Fraction("0.006"), Fraction("1.20")),
    "deepseek-v4-pro": UsdRates(Fraction("1.32"), Fraction("0.044"), Fraction("3.96")),
}


def quote_micro_usd(rates: UsdRates, *, input_tokens: int, output_tokens: int,
                    cached_input_tokens: int = 0) -> int:
    values = (input_tokens, output_tokens, cached_input_tokens)
    if (rates.currency != "USD" or rates.scale != 1_000_000
            or any(type(v) is not int or v < 0 for v in values)
            or cached_input_tokens > input_tokens):
        raise ValueError("USD_USAGE_INVALID")
    # Since scale = tokens-per-price-unit, this exact rational is microUSD.
    amount = ((input_tokens - cached_input_tokens) * rates.input_miss
              + cached_input_tokens * rates.input_hit + output_tokens * rates.output)
    return (amount.numerator + amount.denominator - 1) // amount.denominator


def input_upper_bound(prompt: str) -> int:
    # One text-only user message: UTF-8 byte bound plus generous framing reserve.
    # Not usable for tools, images, multiple messages or hidden reasoning.
    if not isinstance(prompt, str):
        raise ValueError("USD_PROMPT_INVALID")
    return len(prompt.encode("utf-8")) + 256
