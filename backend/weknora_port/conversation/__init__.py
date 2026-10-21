"""Python ordinary-history and personal-memory port (fixed upstream; see NOTICE).

Smart Agent compaction remains a separate pending port. Ordinary history and
memory maintenance do not substitute for the preserved Smart entry.
"""
from .history import OrdinaryHistoryService, attachment_prompt, history_messages, process_history
from .memory import MemoryService
from .management import ManagedMemoryRepository, MemoryManager, MemoryWriteError
from .extraction import ExtractionWorker, InvalidExtractionOutput
from .maintenance import MemoryMaintenance
from .maintenance_types import ExtractionBatch, ExtractionEnqueuer, ExtractionPayload, ExtractionSession, MaintenanceModel, MaintenanceRepository, MessageCursor, ModelRequest, ModelResponse, TopicStat, TranscriptLine, TranscriptSegment
from .topics import TopicService
from .turns import detect_explicit_memory, record_turn_memory
from .types import Attachment, DocumentAffinity, HistoryRound, MemoryConfig, MemoryContext, MemoryEmbedder, MemoryItem, MemoryRecall, MemoryRepository, MemoryScope, MemoryScopeError, MemorySearchResult, MemorySettings, MemorySubject, MemoryTombstone, MemoryVectorHit, MemoryVectorQuery, RetrievalContext, StoredMessage, resolve_scope

PENDING_FEATURES = ("smart_agent_compaction",)

__maintenance_exports__ = ["ExtractionWorker", "InvalidExtractionOutput", "MemoryMaintenance", "ExtractionBatch", "ExtractionEnqueuer", "ExtractionPayload", "ExtractionSession", "MaintenanceModel", "MaintenanceRepository", "MessageCursor", "ModelRequest", "ModelResponse", "TopicService", "TopicStat", "TranscriptLine", "TranscriptSegment"]

__all__ = ["OrdinaryHistoryService", "MemoryService", "MemoryManager", "MemoryWriteError", "ManagedMemoryRepository", "Attachment", "DocumentAffinity", "HistoryRound", "MemoryConfig", "MemoryContext", "MemoryEmbedder", "MemoryItem", "MemoryRecall", "MemoryRepository", "MemoryScope", "MemoryScopeError", "MemorySearchResult", "MemorySettings", "MemorySubject", "MemoryTombstone", "MemoryVectorHit", "MemoryVectorQuery", "RetrievalContext", "StoredMessage", "attachment_prompt", "history_messages", "process_history", "resolve_scope", "PENDING_FEATURES"]
__all__ += __maintenance_exports__ + ["detect_explicit_memory", "record_turn_memory"]
