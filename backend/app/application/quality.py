from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from backend.app.application.query_router import EvidencePlan, EvidenceTarget
from backend.app.application.retrieval import RetrievalResult
from backend.app.domain.models import ChunkRecord
from backend.app.application.structured_evidence import RowFact, row_facts, claim_amount, subject_period_pattern


class QualityReason(StrEnum):
    """Reason codes produced by the deterministic evidence gate."""

    NO_CANDIDATES = "NO_CANDIDATES"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    reason: QualityReason | None = None
    missing_targets: tuple[EvidenceTarget, ...] = ()


class QualityGate:
    def evaluate(self, result: RetrievalResult, plan: EvidencePlan | None = None) -> QualityDecision:
        if not result.items:
            return QualityDecision(False, QualityReason.NO_CANDIDATES)
        return self.evaluate_chunks([item.chunk for item in result.items], plan)

    def evaluate_chunks(self, chunks: list[ChunkRecord] | tuple[ChunkRecord, ...], plan: EvidencePlan | None = None) -> QualityDecision:
        if not chunks:
            return QualityDecision(False, QualityReason.NO_CANDIDATES)
        if plan is not None and plan.kind == "relation" and plan.relation_subjects:
            left, right = plan.relation_subjects
            relationship = re.compile(r"关系|关联|依赖|属于|连接|连接到|组成|包含")
            if not any(
                left in clause and right in clause and relationship.search(clause)
                for chunk in chunks for clause in re.split(r"[。；，,！？\n]", chunk.content)
            ):
                return QualityDecision(False, QualityReason.INSUFFICIENT_EVIDENCE)
        if plan is not None and plan.targets:
            missing = tuple(
                target for target in plan.targets
                if not self.supporting_chunks(chunks, target)
            )
            if missing:
                return QualityDecision(False, QualityReason.INSUFFICIENT_EVIDENCE, missing)
        return QualityDecision(True)

    @staticmethod
    def is_model_caption(locator: dict) -> bool:
        """Recognize derivative provenance before any exact-fact fallback.

        No caption verification path exists here: a self-declared VERIFIED
        label cannot turn model output into independent original evidence.
        """
        if not isinstance(locator, dict):
            return False
        provenance = locator.get("raw_evidence")
        for metadata in (locator, provenance):
            if isinstance(metadata, dict) and (
                metadata.get("evidence_kind") == "model_generated_caption"
                or metadata.get("schema_version") == "local-caption/v1"
                or metadata.get("asset_type") == "caption"
                or metadata.get("content_type") == "image_caption"
            ):
                return True
        return False

    @staticmethod
    def supporting_fact(content: str, target: EvidenceTarget) -> str | None:
        subject = subject_period_pattern(target)
        attribute = re.escape(target.attribute)
        pattern = re.compile(subject + r"[^。；，,！？\n]{0,8}" + attribute + r"[^。；，,！？\n]{0,20}\d", re.IGNORECASE)
        for clause in re.split(r"[。；，,！？\n]", content):
            if pattern.search(clause):
                if target.unit is not None and not re.search(r"\d+(?:\.\d+)?\s*"+re.escape(target.unit)+r"(?![\w])",clause):
                    continue
                return clause.strip()
        return None

    @classmethod
    def supporting_chunks(cls, chunks, target: EvidenceTarget):
        supported=[];signatures=set();structured=False;ambiguous_prose=False
        for chunk in chunks:
            if not getattr(chunk,"is_current",True):continue
            locator=getattr(chunk,"locator",{})
            if cls.is_model_caption(locator):continue
            facts=row_facts(chunk.content,locator,target,getattr(chunk,"content_sha256",None),document_id=chunk.document_id,version_id=chunk.version_id)
            if facts:
                structured=True
                signatures.update((f.value,f.unit) for f in facts)
                supported.append(chunk)
                continue
            # Structured evidence never falls back to matching rendered table text.
            if locator.get("kind")=="table" or target.period is not None and "\t" in chunk.content:continue
            fact=cls.supporting_fact(chunk.content,target)
            if fact:
                supported.append(chunk)
                if target.period is not None:
                    amount=claim_amount(fact,target)
                    if amount is not None:signatures.add(amount)
                    else:ambiguous_prose=True
        if structured and (len(signatures)!=1 or ambiguous_prose):return []
        return supported
