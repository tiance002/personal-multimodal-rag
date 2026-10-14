"""Post-persistence turn hooks: literal remember, cited affinity, auto queue."""
from .types import MemoryItem
from .ranking import sanitize_content
from .unicode15 import lower

PREFIXES = ("记住：", "记住:", "记住，", "记住,", "记住 ", "记住",
            "请记住：", "请记住:", "请记住，", "请记住,", "请记住 ", "请记住",
            "帮我记住：", "帮我记住:", "帮我记住，", "帮我记住,", "帮我记住 ", "帮我记住",
            "remember that ", "remember: ", "remember, ", "please remember that ",
            "please remember: ", "note that ", "keep in mind that ")


def detect_explicit_memory(query):
    query = query.strip()
    for prefix in PREFIXES:
        if lower(query).startswith(lower(prefix)):
            statement = sanitize_content(query[len(prefix):].strip()).lstrip("：:，, ")
            return statement if len(statement) >= 2 else ""
    return ""


async def record_turn_memory(worker, context, session_id, assistant_message_id, user_query,
                             user_message_id, chat_model_id="", verified_sources=(), language=""):
    """Caller invokes after persisted successful turn; refs are server verified.
    Each optional source hook degrades independently. No assistant text is
    distilled and no successful answer is fabricated on maintenance failures.
    """
    statement = detect_explicit_memory(user_query)
    if statement:
        try:
            await worker.manager.remember(context, MemoryItem("", 0, "", "fact", statement, importance=4,
                origin="explicit", source_session_id=session_id, source_message_id=user_message_id,
                operation_id=f"turn:{assistant_message_id}:explicit"))
        except Exception: pass
    scope, cfg, _ = await worker.manager.reader.enabled_scope(context)
    if scope and cfg.retrieval_enabled() and verified_sources:
        try:
            await worker.repository.ensure_subject(scope)
            await worker.repository.bump_doc_affinity(scope, tuple(verified_sources), f"turn:{assistant_message_id}:affinity")
        except Exception: pass
    await worker.schedule(context, session_id, assistant_message_id, chat_model_id, language)
