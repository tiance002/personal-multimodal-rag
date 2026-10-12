"""Conservative, text-only pre-send capacity estimates for selected chat APIs.

This is an injectable contract for the V6 budget gate, not provider routing.
For the pinned DeepSeek V4.1 recipe the bound is the UTF-8 byte length of the
full rendered prompt. It remains conditional on the deployed endpoint using
the same renderer. The tokenizer's exact count is not used because literal
special-token spellings can be treated differently by the recipe tokenizer.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from weakref import WeakValueDictionary
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from backend.app.application.validation_usd_budget import OwnerActivityTokenGrant, TokenRequestBudget


DEEPSEEK_PROVIDER = "deepseek"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_CONTEXT_WINDOW = 1_048_576
DEEPSEEK_MAX_OUTPUT_TOKENS = 393_216
CURRENT_ADAPTER_MAX_OUTPUT_TOKENS = 1_280
V6_OUTPUT_TOKEN_CAP = 512
V6_REQUEST_PROTOCOL = "openai-chat-single-user-text-thinking-disabled-no-tools-v1"
V6_SPECIAL_TOKEN_LITERALS = ("<\uff5cUser\uff5c>", "<think>", "\uff5cDSML\uff5c")
_SPECIAL_TOKEN_CANDIDATE = re.compile(
    r"<(?:(?:[|\uff5c][^>\r\n]{1,128}[|\uff5c])|/?think)>|[\uff5c|]DSML[\uff5c|]"
)
DEEPSEEK_RECIPE_VERSION = "0.1.1"
DEEPSEEK_RECIPE_COMMIT = "8cadfede7063c896b944e7bae05daa3549ae97ea"
DEEPSEEK_TOKENIZER_SHA256 = "81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99"
DEEPSEEK_RENDERER_IDENTITY = (
    f"deepseek-recipe-python/{DEEPSEEK_RECIPE_VERSION}@{DEEPSEEK_RECIPE_COMMIT}"
    f";v41-tokenizer-sha256:{DEEPSEEK_TOKENIZER_SHA256}"
)
SILICONFLOW_XING_MODEL = "XingChenAGI/Xing4.0-29B"
DEEPSEEK_WHEEL_SHA256 = "7818802935becc76ac9b81af6b4a1e5390ec65355cecefd1fd4790f51845c1c5"


class CapacityCounterUnavailable(ValueError):
    """Stable fail-closed reason; never include request contents."""


@dataclass(frozen=True)
class DeepseekCapacityProfile:
    """Explicit Owner-scoped local proof; hosted rendering remains conditional."""
    manifest_path: str
    manifest_sha256: str
    grant: object = field(repr=False)
    evidence_sha256: str
    execution_lease: object | None = field(default=None,repr=False)

    @classmethod
    def from_manifest(cls, path, *, expected_sha256, grant,execution_lease=None):
        from backend.app.application.validation_usd_budget import (
            OwnerActivityTokenGrant, OWNER_V6_TOKEN_GRANT, OWNER_V6_TOKEN_GRANT_R2, OWNER_V6_TOKEN_GRANT_R3,V6ExecutionLease,
        )
        try:
            raw=Path(path).read_bytes()
            if (not re.fullmatch('[0-9a-f]{64}',expected_sha256) or _sha256(raw)!=expected_sha256
                    or type(grant) is not OwnerActivityTokenGrant
                    or grant.execution_kind not in ('REAL','SIMULATED')
                    or grant.execution_kind=='REAL' and grant is not OWNER_V6_TOKEN_GRANT and grant is not OWNER_V6_TOKEN_GRANT_R2 and grant is not OWNER_V6_TOKEN_GRANT_R3
                    or grant.deadline_utc is None and grant.version!=3):
                raise ValueError()
            data=json.loads(raw)
            if grant.version==3:
                if type(execution_lease) is not V6ExecutionLease:raise ValueError()
                execution_lease.validate()
            elif execution_lease is not None:raise ValueError()
            expected=dict(schema_version=1,provider=DEEPSEEK_PROVIDER,model=DEEPSEEK_MODEL,
                endpoint='https://api.deepseek.com/chat/completions',renderer_identity=DEEPSEEK_RENDERER_IDENTITY,
                protocol_id=V6_REQUEST_PROTOCOL,scope_id=grant.scope,grant_sha256=_sha256(_canonical(asdict(grant))),
                deadline_utc=grant.deadline_utc,execution_kind=grant.execution_kind,
                context_window=DEEPSEEK_CONTEXT_WINDOW,output_cap=V6_OUTPUT_TOKEN_CAP,
                bound_basis='OWNER_SCOPED_OFFICIAL_RECIPE_CONDITIONAL')
            if execution_lease is not None:expected['execution_lease']=asdict(execution_lease)
            if (type(data) is not dict or set(data)!=set(expected)|{'resources','evidence_path','evidence_sha256'}
                    or any(type(data[k]) is not type(v) or data[k]!=v for k,v in expected.items())
                    or grant.deadline_utc is not None and datetime.now(timezone.utc)>=datetime.fromisoformat(grant.deadline_utc.replace('Z','+00:00'))):
                raise ValueError()
            resources=data['resources']
            if execution_lease is not None and datetime.now(timezone.utc)>=execution_lease.deadline:raise ValueError()
            hashes={'wheel':DEEPSEEK_WHEEL_SHA256,'tokenizer':DEEPSEEK_TOKENIZER_SHA256}
            if type(resources) is not dict or set(resources)!=set(hashes):raise ValueError()
            for label,digest in hashes.items():
                resource=Path(resources[label])
                if not resource.is_absolute() or _sha256(resource.read_bytes())!=digest:raise ValueError()
            evidence_path=Path(data['evidence_path'])
            evidence_raw=evidence_path.read_bytes()
            if (not evidence_path.is_absolute() or not re.fullmatch('[0-9a-f]{64}',data['evidence_sha256'])
                    or _sha256(evidence_raw)!=data['evidence_sha256']):raise ValueError()
            evidence=json.loads(evidence_raw)
            if type(evidence) is not dict:raise ValueError()
            proof=dict(schema_version=1,provider=DEEPSEEK_PROVIDER,model=DEEPSEEK_MODEL,
                execution_kind=grant.execution_kind,renderer_identity=DEEPSEEK_RENDERER_IDENTITY,
                resource_sha256=hashes,runtime_versions={'deepseek-recipe':DEEPSEEK_RECIPE_VERSION},
                counter_source_sha256=_sha256(Path(__file__).read_bytes()),
                adapter_source_sha256=_sha256(Path(__file__).with_name('deepseek.py').read_bytes()))
            if (any(type(evidence.get(k)) is not type(v) or evidence[k]!=v for k,v in proof.items())
                    or any(type(evidence.get(k)) is not int or evidence[k]<1 for k in ('positive_cases','negative_cases'))
                    or evidence.get('provider_calls')!=0 or type(evidence.get('provider_calls')) is not int):raise ValueError()
            if grant.execution_kind=='REAL':
                import platform,zipfile
                import deepseek_recipe._native as native
                if (platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64')
                        or version('deepseek-recipe')!=DEEPSEEK_RECIPE_VERSION
                        or evidence.get('python_version')!=platform.python_version()
                        or evidence.get('official_renderer') is not True or evidence.get('injected_test_double') is not False):raise ValueError()
                native_raw=Path(native.__file__).read_bytes()
                with zipfile.ZipFile(resources['wheel']) as wheel:
                    members=[n for n in wheel.namelist() if n.startswith('deepseek_recipe/') and n.endswith('.so')]
                    if len(members)!=1 or wheel.read(members[0])!=native_raw:raise ValueError()
                if evidence.get('native_module_sha256')!=_sha256(native_raw):raise ValueError()
            return cls(str(Path(path).resolve()),expected_sha256,grant,data['evidence_sha256'],execution_lease)
        except (OSError,ValueError,TypeError,KeyError,ImportError,PackageNotFoundError):
            raise CapacityCounterUnavailable('DEEPSEEK_CAPACITY_PROFILE_UNVERIFIED') from None

    def revalidate(self):
        current=type(self).from_manifest(self.manifest_path,expected_sha256=self.manifest_sha256,grant=self.grant,execution_lease=self.execution_lease)
        if type(self) is not DeepseekCapacityProfile or current!=self:
            raise CapacityCounterUnavailable('DEEPSEEK_CAPACITY_PROFILE_UNVERIFIED')

    def validate_request(self, *, grant, scope):
        self.revalidate()
        if grant is not self.grant or scope!=self.grant.scope:
            raise CapacityCounterUnavailable('DEEPSEEK_CAPACITY_PROFILE_SCOPE_MISMATCH')


@dataclass(frozen=True)
class TextCapacityEstimate:
    provider: str
    model: str
    scope: str
    scope_sha256: str
    wire_request_sha256: str
    rendered_sha256: str
    renderer_identity: str
    rendered_utf8_bytes: int
    exact_prompt_tokens: int | None
    reservation_bound: int
    output_reservation: int
    context_window: int
    measurement_sha256: str
    bound_status: str
    production_ready: bool
    prompt_text: str
    capacity_profile: DeepseekCapacityProfile | None = field(default=None,repr=False)
    counter: object | None = field(default=None,repr=False,compare=False)

    @property
    def total_reservation_bound(self) -> int:
        return self.reservation_bound + self.output_reservation

    @property
    def fits_context_window(self) -> bool:
        return self.total_reservation_bound <= self.context_window

    def to_token_request_budget(
        self, *, grant: OwnerActivityTokenGrant | None = None, safety_tokens: int = 0,
    ) -> TokenRequestBudget:
        """Build the shared budget-gate DTO without adding another contract."""
        from backend.app.application.validation_usd_budget import (
            OWNER_V6_TOKEN_GRANT,
            TokenRequestBudget,
            V6_CAPACITY_SOURCE,
            V6_CONTEXT_WINDOW_TOKENS,
            V6_REQUEST_PROTOCOL as BUDGET_REQUEST_PROTOCOL,
            V6_SPECIAL_TOKEN_POLICY,
            V6_TOKENIZER_SOURCE,
        )

        grant = OWNER_V6_TOKEN_GRANT if grant is None else grant
        if (type(safety_tokens) is not int or safety_tokens < 0
                or grant.scope != self.scope or grant.provider != self.provider
                or grant.model != self.model or grant.output_token_cap != self.output_reservation
                or BUDGET_REQUEST_PROTOCOL != V6_REQUEST_PROTOCOL
                or V6_SPECIAL_TOKEN_POLICY != "deny-tokenizer-added-token-literals-v1"
                or self.context_window != V6_CONTEXT_WINDOW_TOKENS
                or not self.fits_context_window
                or self.reservation_bound + safety_tokens + self.output_reservation > self.context_window):
            raise CapacityCounterUnavailable("TOKEN_REQUEST_BUDGET_BINDING_INVALID")
        return TokenRequestBudget(
            activity_id=grant.activity_id,
            scope=self.scope,
            provider=self.provider,
            model=self.model,
            input_token_bound=self.reservation_bound,
            output_token_bound=self.output_reservation,
            safety_tokens=safety_tokens,
            window_tokens=self.context_window,
            capacity_source=V6_CAPACITY_SOURCE,
            tokenizer_source=V6_TOKENIZER_SOURCE,
            prompt_text=self.prompt_text,
            special_token_literals=V6_SPECIAL_TOKEN_LITERALS,
            wire_request_sha256=self.wire_request_sha256,
            protocol_id=V6_REQUEST_PROTOCOL,
        )


class DeepseekV41RecipeRenderer:
    """Adapter to the pinned official recipe and tokenizer; never downloads."""

    identity = DEEPSEEK_RENDERER_IDENTITY

    def __init__(self, tokenizer_path: str | Path | None = None):
        self.tokenizer_path = None if tokenizer_path is None else Path(tokenizer_path)
        self.last_token_count: int | None = None

    def __call__(self, body: Mapping[str, Any]) -> str:
        self.last_token_count = None
        try:
            installed = version("deepseek-recipe")
        except PackageNotFoundError:
            raise CapacityCounterUnavailable("DEEPSEEK_RECIPE_NOT_INSTALLED") from None
        if installed != DEEPSEEK_RECIPE_VERSION:
            raise CapacityCounterUnavailable("DEEPSEEK_RECIPE_VERSION_UNVERIFIED")
        if self.tokenizer_path is None:
            raise CapacityCounterUnavailable("TOKENIZER_RESOURCE_PATH_REQUIRED")
        try:
            from deepseek_recipe import (
                ChatCompletionRequest, ConversionOptions, DeepseekV41Encoding, Tokenizer,
            )
            tokenizer_bytes = self.tokenizer_path.read_bytes()
            if hashlib.sha256(tokenizer_bytes).hexdigest() != DEEPSEEK_TOKENIZER_SHA256:
                raise CapacityCounterUnavailable("TOKENIZER_RESOURCE_HASH_MISMATCH")
            request = ChatCompletionRequest(dict(body))
            converted = request.convert(ConversionOptions(default_thinking_mode=False))
            tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
            encoding = DeepseekV41Encoding().with_tokenizer(tokenizer)
            rendered = encoding.render_conversation(converted.conversation)
            if rendered.image_sources:
                raise CapacityCounterUnavailable("MULTIMODAL_RENDER_UNSUPPORTED")
            prompt = rendered.prompt
            # Diagnostic only: the conservative reservation remains the byte
            # bound because recipe token counts can special-case literals.
            self.last_token_count = len(encoding.encode(converted.conversation))
        except CapacityCounterUnavailable:
            raise
        except Exception:
            raise CapacityCounterUnavailable("DEEPSEEK_RECIPE_RENDER_FAILED") from None
        if not isinstance(prompt, str):
            raise CapacityCounterUnavailable("DEEPSEEK_RENDERED_PROMPT_INVALID")
        return prompt


class DeepseekV41TextCapacityCounter:
    """Measure one exact DeepSeekGateway body and return a conditional byte ceiling."""

    def __init__(self, renderer: Callable[[Mapping[str, Any]], str] | None = None, *, production_profile=None):
        self.renderer = renderer or DeepseekV41RecipeRenderer()
        self.renderer_identity = getattr(self.renderer, "identity", None)
        if self.renderer_identity != DEEPSEEK_RENDERER_IDENTITY:
            raise CapacityCounterUnavailable("DEEPSEEK_RENDERER_IDENTITY_UNVERIFIED")
        if production_profile is not None:
            if type(production_profile) is not DeepseekCapacityProfile or type(self.renderer) is not DeepseekV41RecipeRenderer:
                raise CapacityCounterUnavailable('DEEPSEEK_CAPACITY_PROFILE_UNVERIFIED')
            production_profile.revalidate()
            data=json.loads(Path(production_profile.manifest_path).read_bytes())
            if self.renderer.tokenizer_path is None or self.renderer.tokenizer_path.resolve()!=Path(data['resources']['tokenizer']).resolve():
                raise CapacityCounterUnavailable('DEEPSEEK_CAPACITY_PROFILE_UNVERIFIED')
        self.production_profile=production_profile
        self._issued=WeakValueDictionary()

    @property
    def execution_kind(self):
        return self.production_profile.grant.execution_kind if self.production_profile is not None else 'REAL'

    def validate_estimate(self, estimate, *, grant, scope, wire_bytes):
        profile=self.production_profile
        if (type(profile) is not DeepseekCapacityProfile or self._issued.get(id(estimate)) is not estimate
                or estimate.capacity_profile is not profile or estimate.counter is not self
                or estimate.wire_request_sha256!=_sha256(wire_bytes)):
            raise CapacityCounterUnavailable('DEEPSEEK_CAPACITY_PROFILE_UNVERIFIED')
        profile.validate_request(grant=grant,scope=scope)

    def measure(self, provider: str, model: str, body: Mapping[str, Any], *, scope: str) -> TextCapacityEstimate:
        if provider == "siliconflow" and model == SILICONFLOW_XING_MODEL:
            raise CapacityCounterUnavailable("SILICONFLOW_HOSTED_TEMPLATE_UNVERIFIED")
        if provider != DEEPSEEK_PROVIDER or model != DEEPSEEK_MODEL:
            raise CapacityCounterUnavailable("MODEL_COUNTER_UNAVAILABLE")
        if not isinstance(scope, str) or not scope.strip():
            raise CapacityCounterUnavailable("SCOPE_IDENTITY_INVALID")
        self._validate_body(model, body)
        if self.production_profile is not None:
            self.production_profile.validate_request(grant=self.production_profile.grant,scope=scope)
        prompt_text = body["messages"][0]["content"]
        if _SPECIAL_TOKEN_CANDIDATE.search(prompt_text):
            raise CapacityCounterUnavailable("SPECIAL_TOKEN_LITERAL_UNSUPPORTED")
        wire_bytes = _wire_json(body)
        wire_hash = hashlib.sha256(wire_bytes).hexdigest()
        scope_hash = hashlib.sha256(scope.encode("utf-8", errors="strict")).hexdigest()
        try:
            # Reparse the exact bytes sent by CloudAdapter so the renderer and
            # the wire request cannot diverge via a custom Mapping serializer.
            rendered_prompt = self.renderer(json.loads(wire_bytes))
            rendered_bytes = rendered_prompt.encode("utf-8", errors="strict")
        except CapacityCounterUnavailable:
            raise
        except Exception:
            raise CapacityCounterUnavailable("DEEPSEEK_RENDERED_PROMPT_INVALID") from None

        bound = len(rendered_bytes)
        rendered_hash = hashlib.sha256(rendered_bytes).hexdigest()
        material = {
            "provider": provider, "model": model, "scope": scope,
            "scope_sha256": scope_hash, "wire_request_sha256": wire_hash,
            "rendered_sha256": rendered_hash, "renderer_identity": self.renderer_identity,
            "reservation_bound": bound, "output_reservation": body["max_tokens"],
            "context_window": DEEPSEEK_CONTEXT_WINDOW,
        }
        if self.production_profile is not None:
            material['capacity_profile_sha256']=self.production_profile.manifest_sha256
        measurement_hash = _sha256(_canonical(material))
        estimate = TextCapacityEstimate(
            provider=provider, model=model, scope=scope, scope_sha256=scope_hash,
            wire_request_sha256=wire_hash, rendered_sha256=rendered_hash,
            renderer_identity=self.renderer_identity, rendered_utf8_bytes=bound,
            exact_prompt_tokens=None, reservation_bound=bound,
            output_reservation=body["max_tokens"], context_window=DEEPSEEK_CONTEXT_WINDOW,
            measurement_sha256=measurement_hash,
            bound_status="CONDITIONAL_RENDERER_EQUIVALENCE_AND_BYTELEVEL_BPE",
            production_ready=self.production_profile is not None,
            prompt_text=prompt_text,
            capacity_profile=self.production_profile,counter=self if self.production_profile is not None else None,
        )
        if self.production_profile is not None:self._issued[id(estimate)]=estimate
        return estimate

    @staticmethod
    def _validate_body(model: str, body: Mapping[str, Any]) -> None:
        expected = {"model", "messages", "thinking", "max_tokens", "temperature", "stream"}
        if (not isinstance(body, Mapping) or set(body) != expected
                or tuple(body.keys()) != ("model", "messages", "thinking", "max_tokens", "temperature", "stream")):
            raise CapacityCounterUnavailable("REQUEST_ENVELOPE_UNSUPPORTED")
        if body.get("model") != model or body.get("stream") is not False:
            raise CapacityCounterUnavailable("REQUEST_ENVELOPE_UNSUPPORTED")
        if body.get("thinking") != {"type": "disabled"}:
            raise CapacityCounterUnavailable("THINKING_MODE_UNSUPPORTED")
        if type(body.get("temperature")) is not int or body.get("temperature") != 0:
            raise CapacityCounterUnavailable("TEMPERATURE_UNSUPPORTED")
        output_tokens = body.get("max_tokens")
        if (type(output_tokens) is not int or output_tokens != V6_OUTPUT_TOKEN_CAP
                or output_tokens > CURRENT_ADAPTER_MAX_OUTPUT_TOKENS):
            raise CapacityCounterUnavailable("OUTPUT_RESERVATION_UNSUPPORTED")
        messages = body.get("messages")
        if not isinstance(messages, list) or len(messages) != 1:
            raise CapacityCounterUnavailable("SINGLE_USER_TEXT_REQUIRED")
        message = messages[0]
        if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
            raise CapacityCounterUnavailable("MESSAGE_ENVELOPE_UNSUPPORTED")
        role, content = message.get("role"), message.get("content")
        if role != "user" or not isinstance(content, str):
            raise CapacityCounterUnavailable("SINGLE_USER_TEXT_REQUIRED")
        try:
            content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise CapacityCounterUnavailable("MESSAGE_UNICODE_INVALID") from None


def _wire_json(value: Mapping[str, Any]) -> bytes:
    try:
        # Match DeepSeekGateway exactly: json.dumps(payload).encode("utf-8").
        return json.dumps(dict(value)).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise CapacityCounterUnavailable("WIRE_REQUEST_SERIALIZATION_INVALID") from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise CapacityCounterUnavailable("MEASUREMENT_IDENTITY_INVALID") from None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class OrdinaryDeepseekV41TextCapacityCounter(DeepseekV41TextCapacityCounter):
    """Ordinary local framing only; no V6 production profile or grant issuance."""
    endpoint_output_evidence = 'UNKNOWN'
    reviewed_output_caps = (512, 1024)

    def __init__(self, renderer=None):
        super().__init__(renderer)

    @staticmethod
    def _validate_body(model, body):
        output = body.get('max_tokens') if isinstance(body, Mapping) else None
        if type(output) is not int or output not in (512, 1024):
            code = 'OUTPUT_CAPACITY_UNVERIFIED' if type(output) is int and output in (2048, 4096) else 'OUTPUT_CAPACITY_UNSUPPORTED'
            raise CapacityCounterUnavailable(code)
        DeepseekV41TextCapacityCounter._validate_body(model, {**body, 'max_tokens': 512})
