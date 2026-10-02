"""Explicitly authorized synthetic validation, never a product budget fallback."""
import re
import uuid
from datetime import datetime, timezone

from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.usd_pricing import PEAK_RATES, input_upper_bound, quote_micro_usd
from backend.app.ports.session_attempts import AttemptDenied

SYNTHETIC_PROMPT = "Return exactly SYNTHETIC_OK. This is synthetic test data with no private content."
SCOPE = "task-2-authorized-synthetic-validation"
POLICY = {"scope": SCOPE, "token_limit": 1_000_000, "currency": "USD",
          "scale": 1_000_000, "usd_stop_limit": None, "resets": False}


class ValidationAttemptGate(SessionAttemptGate):
    allowed_output_caps = (512,)
    def __init__(self, ledger_path, *, model="deepseek-flash", scope=SCOPE,
                 input_tokens_cap=None, max_output_tokens=512):
        super().__init__(ledger_path)
        self.input_cap = input_upper_bound(SYNTHETIC_PROMPT) if input_tokens_cap is None else input_tokens_cap
        self.output_cap = max_output_tokens
        if (scope != SCOPE or model not in PEAK_RATES or type(self.input_cap) is not int
                or not input_upper_bound(SYNTHETIC_PROMPT) <= self.input_cap <= 1_000_000 - max_output_tokens
                or max_output_tokens not in self.allowed_output_caps):
            raise AttemptDenied("VALIDATION_BOUND_INVALID")
        self.model = model

    def validate_request(self, prompt, timeout_seconds, max_tokens, *, model):
        if (prompt != SYNTHETIC_PROMPT or model != self.model or max_tokens != self.output_cap
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30
                or input_upper_bound(prompt) > self.input_cap):
            raise AttemptDenied("VALIDATION_REQUEST_BOUND_INVALID")

    def _used(self, data):
        if data.get("validation_policy") != POLICY:
            raise AttemptDenied("VALIDATION_POLICY_UNAVAILABLE")
        total = 0
        for row in data["attempts"]:
            tokens, cost = row.get("validation_tokens"), row.get("validation_cost")
            if not isinstance(tokens, dict) or not isinstance(cost, dict):
                raise AttemptDenied("LEGACY_ATTEMPT_COST_OR_TOKENS_UNDEFINED")
            if (cost.get("currency") != "USD" or cost.get("scale") != 1_000_000
                    or tokens.get("state") not in ("reserved", "unknown", "settled")
                    or any(type(tokens.get(k)) is not int or tokens[k] < 0
                           for k in ("reserved_input", "reserved_output"))
                    or type(tokens["reserved_output"]) is not int
                    or not 0 < tokens["reserved_output"] <= 1024
                    or not 0 < tokens["reserved_input"] <= 1_000_000 - tokens["reserved_output"]):
                raise AttemptDenied("VALIDATION_RECORD_INVALID")
            if tokens["state"] == "settled":
                a,b = tokens.get("input"),tokens.get("output")
                if (type(a) is not int or type(b) is not int or not 0 <= a <= tokens["reserved_input"]
                        or not 0 <= b <= tokens["reserved_output"]):
                    raise AttemptDenied("VALIDATION_RECORD_INVALID")
                total += a+b
            else:
                total += tokens["reserved_input"]+tokens["reserved_output"]
        if total > 1_000_000:
            raise AttemptDenied("VALIDATION_RECORD_INVALID")
        return total

    def initialize_disabled_policy(self):
        # Explicit setup only; never enables or resets the shared formal ledger.
        with self._locked():
            data=self._read()
            if data.get("calls_allowed_in_this_task") is not False:
                raise AttemptDenied("VALIDATION_INITIALIZE_REQUIRES_DISABLED")
            if data["attempts"] and data.get("validation_policy") != POLICY:
                raise AttemptDenied("LEGACY_ATTEMPT_COST_OR_TOKENS_UNDEFINED")
            if "validation_policy" in data and data["validation_policy"] != POLICY:
                raise AttemptDenied("VALIDATION_POLICY_INVALID")
            data["validation_policy"]=dict(POLICY)
            self._used(data)
            self._write(data)

    def reserve(self, *, request_id=None):
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",request_id):
            raise AttemptDenied("SESSION_REQUEST_ID_INVALID")
        with self._locked():
            data=self._read()
            if data.get("calls_allowed_in_this_task") is not True:
                raise AttemptDenied("SESSION_CALLS_DISABLED")
            if data.get("validation_blocked_reason"):
                raise AttemptDenied("VALIDATION_USAGE_BOUND_EXCEEDED")
            used=self._used(data)
            self._admit(data,request_id)
            if any(a.get("request_id")==request_id for a in data["attempts"]):
                raise AttemptDenied("SESSION_REQUEST_ALREADY_RESERVED")
            if data["consumed_attempts"]>=data["authorized_limit"]:
                raise AttemptDenied("SESSION_CALL_LIMIT")
            if used+self.input_cap+self.output_cap>1_000_000:
                raise AttemptDenied("VALIDATION_TOKEN_CAP")
            attempt=uuid.uuid4().hex
            data["consumed_attempts"]+=1
            data["reserved_attempts"]+=1
            rates=PEAK_RATES[self.model]
            data["attempts"].append({"attempt_id":attempt,"status":"reserved","request_id":request_id,
                "reserved_utc":datetime.now(timezone.utc).isoformat(),
                "validation_tokens":{"reserved_input":self.input_cap,"reserved_output":self.output_cap,"state":"reserved"},
                "validation_cost":{"currency":"USD","scale":1_000_000,"model":self.model,
                    "reserved_upper_bound_micro_usd":quote_micro_usd(rates,input_tokens=self.input_cap,output_tokens=self.output_cap),
                    "price_source":rates.source,"price_verified_utc":rates.verified_utc,
                    "peak_input_miss_usd_per_million":str(rates.input_miss),
                    "peak_input_hit_usd_per_million":str(rates.input_hit),
                    "peak_output_usd_per_million":str(rates.output),
                    "state":"unknown_charge","actual_charge_verified":False}})
            data["attempts"][-1].update(self._reservation_fields(data))
            self._write(data)
            return attempt

    def _admit(self, data, request_id):
        pass

    def _reservation_fields(self, data):
        return {}

    def finish(self, attempt_id, status):
        self.finish_with_usage(attempt_id,status,{})

    def finish_with_usage(self, attempt_id, status, usage):
        if status not in ("ok","unknown","truncated"):
            raise AttemptDenied("SESSION_ATTEMPT_STATUS_INVALID")
        with self._locked():
            data=self._read();self._used(data)
            row=next((a for a in data["attempts"] if a["attempt_id"]==attempt_id),None)
            if row is None:raise AttemptDenied("SESSION_ATTEMPT_UNKNOWN")
            if row["status"]!="reserved":return
            row["status"]=status;row["finished_utc"]=datetime.now(timezone.utc).isoformat()
            data["reserved_attempts"]-=1
            tokens=row["validation_tokens"];tokens["state"]="unknown"
            if isinstance(usage,dict) and status=="ok":
                a,b=usage.get("prompt_tokens"),usage.get("completion_tokens")
                total=usage.get("total_tokens")
                if ((type(a) is int and a>tokens["reserved_input"])
                        or (type(b) is int and b>tokens["reserved_output"])
                        or (type(total) is int and total>tokens["reserved_input"]+tokens["reserved_output"])):
                    data["validation_blocked_reason"]="VALIDATION_USAGE_BOUND_EXCEEDED"

                valid=(type(a) is int and type(b) is int and a>=0 and b>=0
                       and a<=tokens["reserved_input"] and b<=tokens["reserved_output"]
                       and (total is None or type(total) is int and total==a+b))
                if valid:
                    tokens.update(state="settled",input=a,output=b)
                    cached=usage.get("prompt_cache_hit_tokens",0)
                    if type(cached) is not int or not 0<=cached<=a:cached=0
                    cost=row["validation_cost"]
                    cost.update(state="usage_observed_charge_unverified",
                        observed_usage_peak_upper_bound_micro_usd=quote_micro_usd(PEAK_RATES[self.model],
                            input_tokens=a,output_tokens=b,cached_input_tokens=cached))
            self._write(data)
            if status=="ok" and tokens["state"]!="settled":
                raise AttemptDenied("VALIDATION_USAGE_UNVERIFIED")
