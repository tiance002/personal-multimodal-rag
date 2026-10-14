"""Exact bounded memory_affinity.go consumer, after source rerank/MMR."""
import math
from ..models.types import CallCancelled,RunDeadlineExceeded


def affinity_factor(hits):
    if hits<2:return 1.0
    return 1.0+(1.15-1.0)*min(1.0,math.log1p(hits)/math.log1p(8.0))


async def apply_memory_affinity(state,context,resolver):
    if resolver is None or not state.rerank_results:return
    ids=[];seen=set()
    for row in state.rerank_results:
        if row is None or not row.knowledge_id or row.knowledge_id in seen:continue
        seen.add(row.knowledge_id);ids.append(row.knowledge_id)
        if len(ids)>=200:break
    if not ids:return
    try:affinity=await resolver(tuple(ids))
    except (CallCancelled,RunDeadlineExceeded):raise
    except Exception:return  # Source service read failures return nil.
    if not affinity:return
    boosted=False
    for row in state.rerank_results:
        if row is None:continue
        hits=affinity.get(row.knowledge_id,0)
        if type(hits) is not int or hits<2:continue
        row.score*=affinity_factor(hits);boosted=True
    if boosted:state.rerank_results.sort(key=lambda row:row.score,reverse=True)
