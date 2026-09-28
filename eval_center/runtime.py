"""Local execution provenance read from running components and indexed data.

No database writes or cloud exporters. Reproducing the indexed chunks through
the production chunker detects an old index paired with new input parameters.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from backend.app.domain.chunking import chunk_document
from eval_center.verification import ExperimentInvalidError


def effective_configuration(retriever, store, context_builder, *, declared=None):
    actual={**retriever.effective_config(), 'chunk_size':store.max_chunk_chars,
            'chunk_overlap':store.chunk_overlap, 'context_budget_chars':context_builder.max_chars}
    if declared is not None and declared != actual:
        raise ExperimentInvalidError('effective_config_mismatch')
    return actual


def verify_indexed_chunking(normalized_document, indexed_chunks, store):
    """Verify all frozen source slices; never infer overlap from longest chunk."""
    expected=chunk_document(normalized_document,max_chars=store.max_chunk_chars,overlap=store.chunk_overlap)
    def signature(row):
        return (row['start'],row['end'],row['content'],row['content_sha256'])
    expected_signatures=[signature(chunk.model_dump()) for chunk in expected]
    actual_signatures=[signature(row) for row in indexed_chunks]
    if expected_signatures != actual_signatures:
        raise ExperimentInvalidError('indexed_chunking_mismatch')


def index_fingerprint(rows):
    fields={'document_id','source_version','normalized_sha256','start','end','content_sha256',
            'locator','embedding_digest','embedding_profile'}
    canonical=[]
    for row in rows:
        if not isinstance(row,dict) or not fields <= row.keys():
            raise ExperimentInvalidError('incomplete_index_provenance')
        canonical.append({field:row[field] for field in fields})
    serialized=[json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
                for row in canonical]
    return hashlib.sha256(('\n'.join(sorted(serialized))).encode('utf-8')).hexdigest()


def committed_code_sha(repository:Path):
    def git(*arguments):
        return subprocess.run(['git','-C',str(repository),*arguments],check=True,
                              capture_output=True,text=True).stdout.strip()
    sha=git('rev-parse','HEAD')
    if git('status','--porcelain','--untracked-files=all'):
        raise ExperimentInvalidError('uncommitted_execution_code')
    return sha


def model_identities(gateway):
    """Fetch actual local Ollama inventory; model names alone are insufficient."""
    parsed=urlparse(gateway.base_url)
    if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1','::1'):
        raise ExperimentInvalidError('nonlocal_model_endpoint')
    with urlopen(gateway.base_url.rstrip('/')+'/api/tags',timeout=10) as response:
        inventory=json.load(response)
    result={}
    for role,name in [('chat',gateway.chat_model),('embedding',gateway.embedding_model)]:
        matches=[row for row in inventory.get('models',[]) if row.get('name')==name]
        if len(matches)!=1:
            raise ExperimentInvalidError('model_identity_unavailable')
        digest=matches[0].get('digest')
        if not isinstance(digest,str) or len(digest)!=64 or any(char not in '0123456789abcdef' for char in digest):
            raise ExperimentInvalidError('model_identity_unavailable')
        result[role]={'name':name,'digest':digest}
    return result
