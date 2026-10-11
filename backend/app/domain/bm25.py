"""BM25 over the project's persisted lexical token stream, not Chat tokens.

CJK full runs/bigrams and separate mixed-script runs follow term_frequencies.
PG projects old mixed chunk_terms into this same stream without rewriting rows.
DL is its frequency sum; SQL and Python use identical semantics.
"""
from __future__ import annotations
import math
from collections import Counter
from backend.app.domain.text_normalization import term_frequencies
from backend.app.domain.models import RankedHit

K1 = 1.2
B = 0.75
LEXICAL_VERSION = 'mixed-script-compatible/v2'

def bm25_score(tf: int, df: int, n: int, dl: int, avgdl: float, *, k1=K1, b=B) -> float:
    if tf <= 0 or n <= 0 or avgdl <= 0:
        return 0.0
    return math.log1p((n - df + 0.5) / (df + 0.5)) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))

def score_bm25(chunks, query, limit):
    frequencies = {c.chunk_id: term_frequencies(c.content) for c in chunks}
    lengths = {key: sum(f.values()) for key, f in frequencies.items()}
    n = len(frequencies)
    avgdl = sum(lengths.values()) / n if n else 0
    df = Counter(t for f in frequencies.values() for t in f)
    hits = []
    for key, f in frequencies.items():
        score = sum(bm25_score(f.get(t, 0), df[t], n, lengths[key], avgdl) for t in dict.fromkeys(query.terms))
        if score > 0:
            hits.append(RankedHit(chunk_id=key, rank=1, raw_score=score))
    hits.sort(key=lambda h: (-h.raw_score, h.chunk_id))
    return [h.model_copy(update={'rank':i}) for i,h in enumerate(hits[:limit],1)]
