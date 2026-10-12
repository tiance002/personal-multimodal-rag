"""Offline, pinned text capacity for non-Pro SiliconFlow Xing4.0-29B.

This measures a LOCAL upstream recipe, not SiliconFlow's undisclosed deployment.
No network, credentials, remote Python, model inference or budget admission.
``measure`` accepts bounded requests even while hosted equivalence is UNKNOWN;
its result can never authorize production sending on its own.
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
from typing import Any

from backend.app.ports.providers import ProviderRequestNotSent


SILICONFLOW_PROVIDER = "siliconflow"
SILICONFLOW_XING_MODEL = "XingChenAGI/Xing4.0-29B"
UPSTREAM_REPOSITORY = "XingChen-AGI/Xing4.0-29B-A4B"
UPSTREAM_REVISION = "baae3c3e813cad5f888f1f485cfff659c89076c5"
OFFICIAL_CONTEXT_WINDOW = 262_144  # SF: 256K; pinned upstream config: 262144.
LOCAL_INPUT_TOKEN_CAP = 4_096
LOCAL_OUTPUT_TOKEN_CAP = 512
LOCAL_CONTENT_UTF8_CAP = 8_192
LOCAL_MESSAGE_CAP = 16
LOCAL_WIRE_BYTE_CAP = 65_536
RESOURCE_SHA256 = {
    "tokenizer.model": "fdcbbfdf8655a2e8b8a9fe478de0e9347e857c9b043f9752d04ed660286c3ac5",
    "tokenizer_config.json": "ba3a6872ff07b2260c55b44c7b885a791786eb94baee6ca16290656ab096fa53",
    "chat_template.jinja": "9dfe2d6edfcf86d379d5763582b52e7d618f6035c2d197b29384274653fe2a22",
    "tokenization_xing4_0.py": "4c18df574fad90ee3cadb25586d47b8b32c62c7239e76f246a1bfaedf177799f",
    "config.json": "253e6742d5be0094fd03d79d65cead200de11c5962be9220f8117d978bbe3cc8",
}
RUNTIME_VERSIONS = {"sentencepiece": "0.2.1", "Jinja2": "3.1.6", "MarkupSafe": "3.0.3"}
PROTOCOL_ID = "sf-xing-pinned-text-no-tools-local-cap-v1"
RENDERER_IDENTITY = f"{UPSTREAM_REPOSITORY}@{UPSTREAM_REVISION};{PROTOCOL_ID}"


class XingCapacityUnavailable(ProviderRequestNotSent):
    """Fixed reason code only; request content must never enter exceptions."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def xing_grant_sha256(grant) -> str:
    return _sha256(json.dumps(asdict(grant),sort_keys=True,separators=(',',':'),
        default=lambda value:sorted(value) if type(value) is frozenset else (_ for _ in ()).throw(ValueError())).encode())


@dataclass(frozen=True)
class XingCapacityProfile:
    """Pinned local proof for existing SF grant, never hosted/hidden-CoT proof."""
    manifest_path: str
    manifest_sha256: str
    grant: object = field(repr=False)
    evidence_sha256: str
    execution_lease: object | None = field(default=None,repr=False)

    @classmethod
    def from_manifest(cls,path,*,expected_sha256,grant,execution_lease=None):
        from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT_R3,V6ExecutionLease
        from backend.app.adapters.models.cloud import XingChatGrant
        try:
            raw=Path(path).read_bytes()
            if (type(grant) is not XingChatGrant or grant.execution_kind not in ('REAL','SIMULATED')
                    or not re.fullmatch('[0-9a-f]{64}',expected_sha256) or _sha256(raw)!=expected_sha256):raise ValueError()
            data=json.loads(raw)
            r3=grant.grant_id.startswith(OWNER_V6_TOKEN_GRANT_R3.grant_id+':')
            if r3:
                if type(execution_lease) is not V6ExecutionLease:raise ValueError()
                execution_lease.validate()
            elif execution_lease is not None:raise ValueError()
            expected=dict(schema_version=1,provider=SILICONFLOW_PROVIDER,model=SILICONFLOW_XING_MODEL,
                endpoint='https://api.siliconflow.cn/v1/chat/completions',renderer_identity=RENDERER_IDENTITY,
                protocol_id=PROTOCOL_ID,scope_id=grant.scope_id,grant_sha256=xing_grant_sha256(grant),
                deadline_utc=grant.deadline_utc,execution_kind=grant.execution_kind,context_window=OFFICIAL_CONTEXT_WINDOW,
                input_cap=LOCAL_INPUT_TOKEN_CAP,output_cap=LOCAL_OUTPUT_TOKEN_CAP,content_byte_cap=LOCAL_CONTENT_UTF8_CAP,
                message_cap=LOCAL_MESSAGE_CAP,wire_byte_cap=LOCAL_WIRE_BYTE_CAP,
                bound_basis='OWNER_SCOPED_LOCAL_TWO_MODE_HOSTED_CONDITIONAL')
            if execution_lease is not None:expected['execution_lease']=asdict(execution_lease)
            if (type(data) is not dict or set(data)!=set(expected)|{'resources','evidence_path','evidence_sha256'}
                    or any(type(data[k]) is not type(v) or data[k]!=v for k,v in expected.items())
                    or grant.deadline_utc is None and not grant.grant_id.startswith(OWNER_V6_TOKEN_GRANT_R3.grant_id+':')
                    or grant.deadline_utc is not None and datetime.now(timezone.utc)>=datetime.fromisoformat(grant.deadline_utc.replace('Z','+00:00'))):raise ValueError()
            resources=data['resources']
            if execution_lease is not None and datetime.now(timezone.utc)>=execution_lease.deadline:raise ValueError()
            if type(resources) is not dict or set(resources)!=set(RESOURCE_SHA256):raise ValueError()
            for name,digest in RESOURCE_SHA256.items():
                resource=Path(resources[name])
                if not resource.is_absolute() or resource.name!=name or _sha256(resource.read_bytes())!=digest:raise ValueError()
            evidence_path=Path(data['evidence_path']);evidence_raw=evidence_path.read_bytes()
            if (not evidence_path.is_absolute() or not re.fullmatch('[0-9a-f]{64}',data['evidence_sha256'])
                    or _sha256(evidence_raw)!=data['evidence_sha256']):raise ValueError()
            evidence=json.loads(evidence_raw)
            if type(evidence) is not dict:raise ValueError()
            proof=dict(schema_version=1,provider=SILICONFLOW_PROVIDER,model=SILICONFLOW_XING_MODEL,
                execution_kind=grant.execution_kind,renderer_identity=RENDERER_IDENTITY,
                resource_sha256=RESOURCE_SHA256,runtime_versions=RUNTIME_VERSIONS,local_thinking_modes=[True,False],
                counter_source_sha256=_sha256(Path(__file__).read_bytes()),
                adapter_source_sha256=_sha256(Path(__file__).with_name('cloud.py').read_bytes()))
            if (any(type(evidence.get(k)) is not type(v) or evidence[k]!=v for k,v in proof.items())
                    or any(type(evidence.get(k)) is not int or evidence[k]<1 for k in ('positive_cases','negative_cases'))
                    or evidence.get('provider_calls')!=0 or type(evidence.get('provider_calls')) is not int):raise ValueError()
            for distribution,expected_version in RUNTIME_VERSIONS.items():
                if version(distribution)!=expected_version:raise ValueError()
            if grant.execution_kind=='REAL':
                import platform
                if (platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64')
                        or evidence.get('python_version')!=platform.python_version()
                        or evidence.get('official_renderer') is not True or evidence.get('injected_test_double') is not False):raise ValueError()
            return cls(str(Path(path).resolve()),expected_sha256,grant,data['evidence_sha256'],execution_lease)
        except (OSError,ValueError,TypeError,KeyError,ImportError,PackageNotFoundError):
            raise XingCapacityUnavailable('XING_CAPACITY_PROFILE_UNVERIFIED') from None

    def revalidate(self):
        current=type(self).from_manifest(self.manifest_path,expected_sha256=self.manifest_sha256,grant=self.grant,execution_lease=self.execution_lease)
        if type(self) is not XingCapacityProfile or current!=self:
            raise XingCapacityUnavailable('XING_CAPACITY_PROFILE_UNVERIFIED')

    def validate_request(self,*,grant,scope_id):
        self.revalidate()
        if grant is not self.grant or scope_id!=self.grant.scope_id:
            raise XingCapacityUnavailable('XING_CAPACITY_PROFILE_SCOPE_MISMATCH')


def serialize_xing_body(body: dict[str, Any]) -> bytes:
    """Match current CloudAdapter json.dumps(payload).encode() exactly.

    Preserve insertion order and default ASCII escaping/spaces. Send these bytes,
    or verify the actual final Request.data with ``estimate.assert_wire_bytes``.
    A canonical JSON hash would not bind the actual transport bytes.
    """
    try:
        return json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        raise XingCapacityUnavailable("XING_WIRE_SERIALIZATION_INVALID") from None


@dataclass(frozen=True)
class LocalProfileCount:
    enable_thinking: bool
    exact_prompt_tokens: int
    conservative_prompt_bound: int
    rendered_sha256: str
    token_ids_sha256: str
    rendered_utf8_bytes: int
    generation_prefix: str


@dataclass(frozen=True)
class XingCapacityEstimate:
    provider: str
    model: str
    scope_sha256: str
    wire_request_sha256: str
    measurement_sha256: str
    exact_prompt_tokens: int
    reservation_bound: int
    output_reservation: int
    selected_local_thinking: bool
    profiles: tuple[LocalProfileCount, ...]
    wire_bytes: bytes = field(repr=False)
    renderer_identity: str = RENDERER_IDENTITY
    context_window: int = OFFICIAL_CONTEXT_WINDOW
    local_input_token_cap: int = LOCAL_INPUT_TOKEN_CAP
    hosted_revision: str = field(default="UNKNOWN", init=False)
    hosted_input_token_bound: None = field(default=None, init=False)
    hosted_total_token_bound: None = field(default=None, init=False)
    bound_status: str = field(default="LOCAL_PROVEN_HOSTED_CONDITIONAL", init=False)
    production_ready: bool = field(default=False, init=False)
    capacity_profile: XingCapacityProfile | None = field(default=None,repr=False)
    counter: object | None = field(default=None,repr=False,compare=False)

    def __post_init__(self):
        if self.capacity_profile is not None:
            object.__setattr__(self,'production_ready',type(self.capacity_profile) is XingCapacityProfile)

    def validate_profile(self,grant):
        if type(self.counter) is not SiliconFlowXingCapacityCounter:
            raise XingCapacityUnavailable('XING_HOSTED_CAPACITY_AND_THINKING_UNVERIFIED')
        self.counter.validate_estimate(self,grant=grant)

    @property
    def local_total_reservation_bound(self) -> int:
        """Local prompt plus requested answer only; hidden hosted CoT is UNKNOWN."""
        return self.reservation_bound + self.output_reservation

    @property
    def fits_local_policy(self) -> bool:
        return (self.reservation_bound <= self.local_input_token_cap
                and self.output_reservation <= LOCAL_OUTPUT_TOKEN_CAP
                and self.local_total_reservation_bound <= self.context_window)

    def assert_wire_bytes(self, final_bytes: bytes) -> None:
        if type(final_bytes) is not bytes or final_bytes != self.wire_bytes:
            raise XingCapacityUnavailable("XING_FINAL_WIRE_MISMATCH")


class SiliconFlowXingCapacityCounter:
    """Hash-verified upstream SentencePiece + sandboxed official Jinja template.

    Assets and the three pinned optional packages must already exist locally.
    The downloaded Python file is HASHED AS EVIDENCE ONLY, never imported/run.
    Mirrors the audited slow tokenizer's added-token split + deterministic
    SentencePiece encode, with no extra BOS/EOS and no truncation/sampling.
    """

    identity = RENDERER_IDENTITY

    def __init__(self, resource_directory: str | Path, *, production_profile=None, ordinary_service=False):
        if type(ordinary_service) is not bool or (ordinary_service and production_profile is not None):
            raise XingCapacityUnavailable('XING_CAPACITY_PROFILE_UNVERIFIED')
        # Frozen evaluation keeps its small local limits. Ordinary service uses
        # the hash-verified upstream window, with the same exact rendering and
        # normalized-byte proof; this does not assert hosted equivalence.
        self.local_input_token_cap = OFFICIAL_CONTEXT_WINDOW - LOCAL_OUTPUT_TOKEN_CAP if ordinary_service else LOCAL_INPUT_TOKEN_CAP
        self.local_content_utf8_cap = OFFICIAL_CONTEXT_WINDOW if ordinary_service else LOCAL_CONTENT_UTF8_CAP
        self.local_wire_byte_cap = 6 * OFFICIAL_CONTEXT_WINDOW + LOCAL_WIRE_BYTE_CAP if ordinary_service else LOCAL_WIRE_BYTE_CAP
        self.identity = RENDERER_IDENTITY + (';ordinary-window/v1' if ordinary_service else '')
        if production_profile is not None:
            if type(production_profile) is not XingCapacityProfile:
                raise XingCapacityUnavailable('XING_CAPACITY_PROFILE_UNVERIFIED')
            production_profile.revalidate()
            paths=json.loads(Path(production_profile.manifest_path).read_bytes())['resources']
            if any((Path(resource_directory)/name).resolve()!=Path(paths[name]).resolve() for name in RESOURCE_SHA256):
                raise XingCapacityUnavailable('XING_CAPACITY_PROFILE_UNVERIFIED')
        self.production_profile=production_profile
        self._issued=WeakValueDictionary()
        resources = {}
        for name, expected_hash in RESOURCE_SHA256.items():
            try:
                data = (Path(resource_directory) / name).read_bytes()
            except OSError:
                raise XingCapacityUnavailable("XING_RESOURCE_UNAVAILABLE") from None
            if _sha256(data) != expected_hash:
                raise XingCapacityUnavailable("XING_RESOURCE_HASH_MISMATCH")
            resources[name] = data
        try:
            for distribution, expected_version in RUNTIME_VERSIONS.items():
                if version(distribution) != expected_version:
                    raise XingCapacityUnavailable("XING_RUNTIME_VERSION_UNVERIFIED")
            import sentencepiece
            from jinja2.sandbox import ImmutableSandboxedEnvironment

            config = json.loads(resources["tokenizer_config.json"])
            if (config["add_bos_token"] is not False or config["add_eos_token"] is not False
                    or config["split_special_tokens"] is not False or config["sp_model_kwargs"]
                    or json.loads(resources["config.json"])["max_position_embeddings"] != OFFICIAL_CONTEXT_WINDOW):
                raise XingCapacityUnavailable("XING_LOCAL_RECIPE_UNSUPPORTED")
            self._processor = sentencepiece.SentencePieceProcessor(model_proto=resources["tokenizer.model"])
            self._special_ids = {row["content"]: int(key)
                                 for key, row in config["added_tokens_decoder"].items()}
            for spelling, token_id in self._special_ids.items():
                if self._processor.piece_to_id(spelling) != token_id:
                    raise XingCapacityUnavailable("XING_SPECIAL_TOKEN_ID_MISMATCH")
            # Each normal piece consumes a nonempty span; a byte fallback piece
            # consumes one normalized UTF-8 byte. Control pieces are split out.
            if any(not self._processor.id_to_piece(i) for i in range(self._processor.get_piece_size())):
                raise XingCapacityUnavailable("XING_EMPTY_VOCABULARY_PIECE")
            self._split_special = re.compile("(" + "|".join(
                re.escape(s) for s in sorted(self._special_ids, key=len, reverse=True)) + ")")
            environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
            self._template = environment.from_string(resources["chat_template.jinja"].decode("utf-8"))
        except XingCapacityUnavailable:
            raise
        except (ImportError, PackageNotFoundError):
            raise XingCapacityUnavailable("XING_RUNTIME_UNAVAILABLE") from None
        except Exception:
            raise XingCapacityUnavailable("XING_LOCAL_RECIPE_LOAD_FAILED") from None

    @classmethod
    def from_directory(cls, path: str | Path, *, production_profile=None) -> SiliconFlowXingCapacityCounter:
        return cls(path,production_profile=production_profile)

    def validate_estimate(self,estimate,*,grant):
        profile=self.production_profile
        if (type(profile) is not XingCapacityProfile or self._issued.get(id(estimate)) is not estimate
                or estimate.capacity_profile is not profile or estimate.counter is not self
                or estimate.scope_sha256!=_sha256(grant.scope_id.encode())):
            raise XingCapacityUnavailable('XING_HOSTED_CAPACITY_AND_THINKING_UNVERIFIED')
        profile.validate_request(grant=grant,scope_id=grant.scope_id)

    def _validate_body(self, model: str, body: dict[str, Any]) -> None:
        required = {"model", "messages", "max_tokens", "stream"}
        if (type(body) is not dict or set(body) not in (required, required | {"enable_thinking"})
                or body.get("model") != model or body.get("stream") is not False):
            raise XingCapacityUnavailable("XING_REQUEST_ENVELOPE_UNSUPPORTED")
        if "enable_thinking" in body and body["enable_thinking"] is not False:
            raise XingCapacityUnavailable("XING_THINKING_FIELD_UNSUPPORTED")
        if type(body["max_tokens"]) is not int or not 1 <= body["max_tokens"] <= LOCAL_OUTPUT_TOKEN_CAP:
            raise XingCapacityUnavailable("XING_OUTPUT_LOCAL_CAP_EXCEEDED")
        messages = body["messages"]
        if type(messages) is not list or not 1 <= len(messages) <= LOCAL_MESSAGE_CAP:
            raise XingCapacityUnavailable("XING_MESSAGES_UNSUPPORTED")
        content_bytes = 0
        for message in messages:
            if (type(message) is not dict or set(message) != {"role", "content"}
                    or type(message["role"]) is not str
                    or message["role"] not in {"system", "user", "assistant"}
                    or type(message["content"]) is not str):
                raise XingCapacityUnavailable("XING_MESSAGE_UNSUPPORTED")
            try:
                content_bytes += len(message["content"].encode("utf-8", errors="strict"))
            except UnicodeError:
                raise XingCapacityUnavailable("XING_TEXT_UNICODE_INVALID") from None
            if any(s in message["content"] for s in self._special_ids):
                # Do not reinterpret user text as control/reasoning/tool markers.
                raise XingCapacityUnavailable("XING_SPECIAL_TOKEN_LITERAL_UNSUPPORTED")
        if content_bytes > self.local_content_utf8_cap:
            raise XingCapacityUnavailable("XING_CONTENT_LOCAL_CAP_EXCEEDED")

    def _count_rendered(self, rendered: str, thinking: bool) -> LocalProfileCount:
        ids, bound = [], 0
        for segment in self._split_special.split(rendered):
            if not segment:
                continue
            if segment in self._special_ids:
                ids.append(self._special_ids[segment])
                bound += 1
            else:
                # Bound the ACTUAL normalized bytes, including dummy whitespace
                # prefix and any normalization expansion; no raw chars/token
                # ratio. BPE/Unigram pieces partition normalized spans; fallback
                # expands at most to their UTF-8 bytes; UNK consumes a span.
                normalized_bytes = self._processor.normalize(segment).encode("utf-8", errors="strict")
                bound += len(normalized_bytes)
                ids.extend(self._processor.encode(segment, out_type=int, enable_sampling=False,
                                                  add_bos=False, add_eos=False))
        if not 0 < len(ids) <= bound:
            raise XingCapacityUnavailable("XING_NORMALIZED_BOUND_INVALID")
        prefix = "<_bot><think>\n" if thinking else "<_bot></think>"
        if not rendered.endswith(prefix):
            raise XingCapacityUnavailable("XING_GENERATION_PREFIX_INVALID")
        return LocalProfileCount(
            enable_thinking=thinking, exact_prompt_tokens=len(ids), conservative_prompt_bound=bound,
            rendered_sha256=_sha256(rendered.encode("utf-8")),
            token_ids_sha256=_sha256(json.dumps(ids, separators=(",", ":")).encode("ascii")),
            rendered_utf8_bytes=len(rendered.encode("utf-8")), generation_prefix=prefix,
        )

    def measure(self, provider: str, model: str, body: dict[str, Any], *, scope: str,
                wire_bytes: bytes | None = None) -> XingCapacityEstimate:
        """Measure complete messages/framing/prefix against small LOCAL limits.

        Missing enable_thinking selects the upstream template's thinking default
        locally, without asserting SF's default. Count both pinned modes and use
        their maximum conservative bound. Explicit False is ALSO conditional on
        SF honoring it for this exact model; max_tokens excludes hosted CoT.
        """
        if provider != SILICONFLOW_PROVIDER or model != SILICONFLOW_XING_MODEL:
            raise XingCapacityUnavailable("XING_MODEL_COUNTER_UNAVAILABLE")
        if type(scope) is not str or not scope.strip():
            raise XingCapacityUnavailable("XING_SCOPE_INVALID")
        try:
            scope_bytes = scope.encode("utf-8", errors="strict")
        except UnicodeError:
            raise XingCapacityUnavailable("XING_SCOPE_INVALID") from None
        self._validate_body(model, body)
        if self.production_profile is not None:
            self.production_profile.validate_request(grant=self.production_profile.grant,scope_id=scope)
            if body.get('enable_thinking') is not False:
                raise XingCapacityUnavailable('XING_THINKING_FIELD_UNSUPPORTED')
        serialized = serialize_xing_body(body)
        if len(serialized) > self.local_wire_byte_cap:
            raise XingCapacityUnavailable("XING_WIRE_LOCAL_CAP_EXCEEDED")
        if wire_bytes is not None and (type(wire_bytes) is not bytes or wire_bytes != serialized):
            raise XingCapacityUnavailable("XING_WIRE_SERIALIZATION_MISMATCH")
        # Render a snapshot of exactly the serialized body, not mutable callers.
        frozen_body = json.loads(serialized)
        try:
            profiles = tuple(self._count_rendered(self._template.render(
                messages=frozen_body["messages"], tools=[], add_generation_prompt=True,
                enable_thinking=thinking), thinking) for thinking in (True, False))
        except XingCapacityUnavailable:
            raise
        except Exception:
            raise XingCapacityUnavailable("XING_TEMPLATE_OR_TOKENIZATION_FAILED") from None
        bound = max(p.conservative_prompt_bound for p in profiles)
        if bound > self.local_input_token_cap:
            raise XingCapacityUnavailable("XING_INPUT_LOCAL_CAP_EXCEEDED")
        if bound + frozen_body["max_tokens"] > OFFICIAL_CONTEXT_WINDOW:
            raise XingCapacityUnavailable("XING_LOCAL_CONTEXT_WINDOW_EXCEEDED")
        selected_thinking = "enable_thinking" not in frozen_body
        selected = next(p for p in profiles if p.enable_thinking == selected_thinking)
        material = dict(provider=provider, model=model, scope_sha256=_sha256(scope_bytes),
                        wire_request_sha256=_sha256(serialized), renderer_identity=self.identity,
                        resources=RESOURCE_SHA256, runtime=RUNTIME_VERSIONS,
                        profile_hashes=[p.token_ids_sha256 for p in profiles],
                        reservation_bound=bound, output_reservation=frozen_body["max_tokens"])
        if self.production_profile is not None:
            material['capacity_profile_sha256']=self.production_profile.manifest_sha256
        estimate = XingCapacityEstimate(
            provider=provider, model=model, scope_sha256=material["scope_sha256"],
            wire_request_sha256=material["wire_request_sha256"],
            measurement_sha256=_sha256(json.dumps(material, sort_keys=True).encode("ascii")),
            exact_prompt_tokens=selected.exact_prompt_tokens, reservation_bound=bound,
            output_reservation=frozen_body["max_tokens"], selected_local_thinking=selected_thinking,
            profiles=profiles, wire_bytes=serialized,capacity_profile=self.production_profile,
            counter=self if self.production_profile is not None else None,
            renderer_identity=self.identity, local_input_token_cap=self.local_input_token_cap,
        )
        if self.production_profile is not None:self._issued[id(estimate)]=estimate
        return estimate

    def __call__(self, envelope: dict[str, Any]) -> int:
        """ModelWindow's pre-count interface; not final send admission.

        Accept only {model, messages, tools: []}. The sender still MUST measure
        its final body/output and bind Request.data separately. This local bound
        cannot establish hosted tokenizer/template/thinking or non-truncation.
        """
        if (type(envelope) is not dict or set(envelope) != {"model", "messages", "tools"}
                or type(envelope["tools"]) is not list or envelope["tools"]):
            raise XingCapacityUnavailable("XING_WINDOW_ENVELOPE_UNSUPPORTED")
        body = dict(model=envelope["model"], messages=envelope["messages"], max_tokens=1, stream=False)
        if self.production_profile is not None:body['enable_thinking']=False
        return self.measure(SILICONFLOW_PROVIDER, envelope["model"], body,
                            scope=self.production_profile.grant.scope_id if self.production_profile is not None
                            else "LOCAL_MODEL_WINDOW_PRECOUNT").reservation_bound


class OrdinarySiliconFlowXingCapacityCounter(SiliconFlowXingCapacityCounter):
    """Local ordinary protocol proof, separate from frozen grants and profiles."""
    endpoint_output_evidence = 'UNKNOWN'
    reviewed_output_caps = (512, 1024)

    def __init__(self, resource_directory):
        super().__init__(resource_directory, ordinary_service=True)
        self.local_input_token_cap = OFFICIAL_CONTEXT_WINDOW - 1024
        self.identity += ';ordinary-output/v1;endpoint-output=UNKNOWN'

    def _validate_body(self, model, body):
        output = body.get('max_tokens') if type(body) is dict else None
        if type(output) is not int or output not in self.reviewed_output_caps:
            code = 'OUTPUT_CAPACITY_UNVERIFIED' if type(output) is int and output in (2048, 4096) else 'OUTPUT_CAPACITY_UNSUPPORTED'
            raise XingCapacityUnavailable(code)
        # Reuse the complete frozen protocol validation without widening it.
        super()._validate_body(model, {**body, 'max_tokens': 512})
