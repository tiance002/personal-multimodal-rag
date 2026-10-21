"""Native ordinary-chain captured Context pairing; no legacy QA executor."""
from __future__ import annotations
from dataclasses import dataclass,replace,asdict
import asyncio,hashlib,json,time
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.generation.context import prepare_generation

MODES=('OFF','CHEAP_ONLY','EXPENSIVE_ONLY','DYNAMIC')

def capture_hash(prepared):
    row=asdict(prepared.request)
    for field in ('run_id','session_id','message_id','policy'):row.pop(field)
    return hashlib.sha256(json.dumps({'input':row,'scope':asdict(prepared.context.scope),
        'required_origin_kbs':prepared.context.required_origin_kbs,'rankings':prepared.rankings},ensure_ascii=False,
        sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class PairedExecution:
    capture_sha256:str
    prompt_sha256:str
    context_sha256:str
    outcomes:tuple[dict,...]
    rankings:tuple
    preparation_seconds:float
    execution_kind:str
    model_quality_status:str
    auxiliary_calls:tuple[dict,...]


async def execute_native_pair(application,turns,*,execution_kind):
    """Caller supplies4 freshly admitted server-owned turns for the same query.

    No model/cost authority is created here. Every ordinary preparation/model
    send uses ApplicationRuntime's existing registry and gate. This interface
    does not authorize real calls or expose a router-mode API.
    """
    if execution_kind not in ('ISOLATED_MOCK','REAL_PROVIDER'):raise ValueError('EVALUATION_EXECUTION_KIND_REQUIRED')
    if len(turns)!=4 or len({t.run_id for t in turns})!=4:raise ValueError('PAIRED_FOUR_DISTINCT_ADMISSIONS_REQUIRED')
    first=turns[0]
    for turn in turns:
        application.ordinary._validate(turn)
        if turn.replay_only or not turn.is_new or turn.context.scope!=first.context.scope or turn.memory_context!=first.memory_context or turn.session_id!=first.session_id or turn.request.content!=first.request.content:
            raise ProviderError('PAIRED_SERVER_SCOPE_OR_QUERY_MISMATCH')
    run_ids=tuple(turn.run_id for turn in turns)
    async def ledger_snapshot():
        rows=await application._rows('SELECT request_id,provider,model,capability,state,usage,reserved_microunits,actual_microunits,price_source FROM send_calls WHERE tenant_id=%s AND subject_id=%s',
            (application.principal.tenant_id,application.principal.subject_id))
        # Owner-window alone could include unrelated background jobs.
        # All ordinary sends use an exact admitted run or run/operation ID.
        return {row['request_id']:row for row in rows
            if any(row['request_id']==run or row['request_id'].startswith(run+'/') for run in run_ids)}
    before=await ledger_snapshot()
    tick=time.perf_counter()
    try:prepared=await application.ordinary.prepare_turn(first)
    except BaseException:
        state=application._runs.pop(first.run_id,None)
        if state is not None:await state['registry'].close()
        raise
    preparation_seconds=time.perf_counter()-tick
    after_preparation=await ledger_snapshot()
    auxiliary=tuple(row for key,row in after_preparation.items() if key not in before)
    fingerprint=capture_hash(prepared);generation=prepare_generation(prepared.request)
    outcomes=[];base_state=application._runs[first.run_id]
    try:
        for mode,turn in zip(MODES,turns):
            request=replace(prepared.request,run_id=turn.run_id,message_id=turn.assistant_message_id,
                session_id=turn.session_id,policy=replace(prepared.request.policy,mode=mode))
            context=replace(prepared.context,request_id=turn.run_id,cancelled=turn.context.cancelled)
            current=prepare_generation(request)
            if current.messages!=generation.messages or current.prompt_sha256!=generation.prompt_sha256 or current.context_sha256!=generation.context_sha256:
                raise ProviderError('PAIRED_CAPTURE_NOT_IDENTICAL')
            if turn.run_id in application._runs and application._runs[turn.run_id] is not base_state:
                raise ProviderError('PAIRED_ACTIVE_RUN_CONFLICT')
            application._runs[turn.run_id]=base_state
            async def progress(stage,status,**fields):await application.ordinary._progress(turn,stage,status,**fields)
            coordinator=await application._generation(turn,progress,policy=request.policy)
            prior_calls=await ledger_snapshot()
            start=time.perf_counter()
            try:
                result=await coordinator.run(request,context)
                if result.prompt_sha256!=generation.prompt_sha256 or result.context_sha256!=generation.context_sha256:
                    raise ProviderError('PAIRED_RESULT_CAPTURE_CHANGED')
                outcomes.append({'mode':mode,'run_id':turn.run_id,'assistant_message_id':turn.assistant_message_id,
                    'status':'COMPLETED','result':result,'generation_seconds':time.perf_counter()-start})
            except (Exception,asyncio.CancelledError) as error:
                await application.ordinary.publish_error(turn,error)
                outcomes.append({'mode':mode,'run_id':turn.run_id,'status':'FAILED',
                    'error_code':application.ordinary._safe_code(error),'generation_seconds':time.perf_counter()-start})
                if isinstance(error,asyncio.CancelledError):raise
            finally:application._runs.pop(turn.run_id,None)
            current_calls=await ledger_snapshot()
            outcomes[-1]['model_calls']=tuple(row for key,row in current_calls.items() if key not in prior_calls)
        if capture_hash(prepared)!=fingerprint:raise ProviderError('PAIRED_CAPTURE_MUTATED')
        return PairedExecution(fingerprint,generation.prompt_sha256,generation.context_sha256,tuple(outcomes),
            prepared.rankings,preparation_seconds,execution_kind,
            'NOT_RUN_SIMULATED_MODELS' if execution_kind=='ISOLATED_MOCK' else 'RECORDED_RESULTS_REQUIRE_RECEIPT_VERIFICATION',auxiliary)
    finally:
        # Setup can fail before the per-mode finally; remove only shared state.
        for turn in turns:
            if application._runs.get(turn.run_id) is base_state:
                application._runs.pop(turn.run_id,None)
        await base_state['registry'].close()
