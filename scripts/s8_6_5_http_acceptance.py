"""S8.6.5 test-only composition over the existing formal FastAPI launcher.

The HTTP collector, Session/Message routes, P5, Registry, SendGate and execution
ledger remain the existing implementations. This module only selects the
versioned candidate prompt and binds the new acceptance profile.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from scripts.s8_6_1_api_acceptance import (
    compose_configuration as compose_prompt_b, install_profile as install_existing_profile,
    digest, require, write_new,
)
from backend.weknora_port.generation.prompt_candidate import candidate_profile

PROFILE_ID = 's8.6.5-http-candidate/v1'


def compose_configuration(base):
    require(base.pipeline_state.retrieval_strategy_mode=='query_structure_v1'
        and base.pipeline_state.source_evidence_mode=='bounded_source_v1', 'S865_SAVED_SESSION_MODES_REQUIRED')
    result = compose_prompt_b(base)
    options = dict(result.generation_options)
    options['system_prompt'] += '\n\n' + candidate_profile()['suffix']
    return replace(result, generation_options=options)


def install_profile(runtime, directory=None, **kwargs):
    """Reuse all original per-turn scope/query/model checks before adding text."""
    install_existing_profile(runtime, directory, **kwargs)
    original = runtime.ordinary.configuration

    async def configuration(turn):
        result = await original(turn)  # Already composed Prompt B exactly once.
        require(result.pipeline_state.retrieval_strategy_mode=='query_structure_v1'
            and result.pipeline_state.source_evidence_mode=='bounded_source_v1', 'S865_SAVED_SESSION_MODES_REQUIRED')
        options = dict(result.generation_options)
        suffix = candidate_profile()['suffix']
        require(suffix not in options['system_prompt'], 'S865_CANDIDATE_ALREADY_APPLIED')
        options['system_prompt'] += '\n\n' + suffix
        if directory is not None:
            write_new(Path(directory) / (digest(turn.run_id.encode()) + '.s865-profile.json'), {
                'profile': PROFILE_ID, 'run_id':turn.run_id,
                'system_prompt_sha256':digest(options['system_prompt'].encode()),
                'candidate_suffix_sha256':hashlib.sha256(suffix.encode()).hexdigest(),
                'citations_enabled':options['citations_enabled'],
                'proof_boundary':'configuration only; actual Provider wire, SSE, PG and Cloud readback required'})
        return replace(result, generation_options=options)

    runtime.ordinary.configuration = configuration
    return runtime


def profile_manifest():
    profile = candidate_profile()
    return {'profile':PROFILE_ID, 'test_only':True,
        'prompt_b_suffix_sha256':profile['baseline_prompt_b_suffix_sha256'],
        'candidate_suffix_sha256':profile['suffix_sha256'],
        'retrieval_strategy_mode':'query_structure_v1', 'source_evidence_mode':'bounded_source_v1',
        'citations_enabled':True, 'p5_mode':'DYNAMIC', 'rerank_top1_floorless':True,
        'enable_expansion':False, 'max_http_submissions':2, 'max_provider_attempts_per_question':12,
        'accounting_mode':'reported_usage_only_v1', 'model_calls_authorized':False}


def validate_limits(approval, ledger=None):
    require(type(approval.get('max_calls')) is int and 1 <= approval['max_calls'] <= 12
        and type(approval.get('max_tokens')) is int and approval['max_tokens'] > 0,
        'S865_EXPLICIT_GRANT_LIMITS_REQUIRED')
    if ledger is not None:
        require(ledger.get('max_calls')==approval['max_calls']
            and ledger.get('max_tokens')==approval['max_tokens'], 'S865_GRANT_LEDGER_LIMIT_MISMATCH')


def create_application(environment=None, *, assemble_factory=None):
    """Isolated temporary API; a fresh Owner child grant is required to send."""
    from backend.weknora_port.development import assemble
    from scripts.s8_ecs_runtime import create_application as formal_application
    env = dict(os.environ if environment is None else environment)
    require(env.get('WEKNORA_PORT_REDIS_PREFIX','').startswith('weknora-python-s8:s865:'),
            'S865_ISOLATED_REDIS_PREFIX_REQUIRED')
    require(env.get('S8_COMPONENT','api')=='api', 'S865_API_ONLY')
    require(env.get('WEKNORA_PORT_ROUTER_MODE','DYNAMIC')=='DYNAMIC', 'S865_ORIGINAL_P5_REQUIRED')
    require(env.get('WEKNORA_PORT_GRAPH_ENABLED','false').lower()=='false', 'S865_GRAPH_DISABLED_REQUIRED')
    approval = None
    if env.get('WEKNORA_PORT_CLOUD_ALLOWED','false').lower()=='true':
        approval = json.loads(Path(env['S865_HTTP_GRANT_FILE']).read_text('utf8'))
        require(approval.get('owner_approved') is True and approval.get('status')=='OPEN'
            and approval.get('scope')=='S8_6_5_HTTP'
            and approval.get('profile_sha256')==digest(profile_manifest())
            and approval.get('source_commit')==env.get('S8_SOURCE_COMMIT')
            and approval.get('source_commit') and approval.get('image_id')==env.get('S865_IMAGE_ID')
            and approval.get('image_id'), 'S865_FRESH_SOURCE_PROFILE_GRANT_REQUIRED')
        expires = datetime.fromisoformat(approval['expires_at'].replace('Z','+00:00'))
        require(expires > datetime.now(timezone.utc), 'S865_HTTP_GRANT_EXPIRED')
        require(approval.get('accounting_mode')=='reported_usage_only_v1'
            and len(approval.get('turns',[]))==1, 'S865_SINGLE_NEW_SESSION_TURN_REQUIRED')
        validate_limits(approval)
        # The child grant is claimed once, before runtime can send anything.
        claim = Path(approval['execution_file']).with_suffix('.s865-api-claim')
        with claim.open('x',encoding='utf8') as stream:
            stream.write(digest(approval))

    @asynccontextmanager
    async def factory(settings, **kwargs):
        async with (assemble_factory or assemble)(settings, **kwargs) as runtime:
            require(not runtime.graph_enabled and runtime.conversation.bridge_rescue_mode=='off',
                    'S865_ORIGINAL_EXPERIMENT_DEFAULTS_REQUIRED')
            if approval is not None:
                from backend.weknora_port.execution import LocalExecution
                require(isinstance(getattr(runtime,'execution',None),LocalExecution), 'S865_EXECUTION_REQUIRED')
                with runtime.execution._ledger() as ledger:
                    validate_limits(approval, ledger)
                    require(ledger['status']=='OPEN' and not ledger['entries']
                        and ledger['max_calls']<=12 and runtime.execution.accounting_mode=='reported_usage_only_v1',
                        'S865_FRESH_CHILD_LEDGER_REQUIRED')
                require(str(settings.execution_file)==approval['execution_file'], 'S865_CHILD_LEDGER_IDENTITY_REQUIRED')
            install_profile(runtime, env.get('S861_AUDIT_DIR'),
                approved_batch=approval, expires_at=expires if approval else None,
                bounded_query_profile=True,
                approved_scope=approval['approved_scope'] if approval else None)
            yield runtime
    return formal_application(env, assemble_factory=factory)
