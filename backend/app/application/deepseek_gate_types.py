"""Closed DeepSeek gate identities; transport receives this through composition."""
from backend.app.ports.deepseek_gate_types import DeepSeekGateTypes
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.application.rag_validation_scope import RagValidationGate
from backend.app.application.static_pair_validation import StaticPairValidationGate
from backend.app.application.reviewed_immutable_batch import ReviewedImmutableBatchGate
from backend.app.application.reviewed_product_request import ReviewedProductRequestGate, ProductReceiptFileSink

DEEPSEEK_GATE_TYPES = DeepSeekGateTypes(
    SessionAttemptGate, ValidationAttemptGate, RagValidationGate,
    StaticPairValidationGate, ReviewedImmutableBatchGate,
    ReviewedProductRequestGate, ProductReceiptFileSink,
)
