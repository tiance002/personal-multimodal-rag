"""Observation-only paired reports; no provider dispatch or billing mutations."""
from __future__ import annotations
import math
import re
import time
from collections import Counter
from langchain_core.runnables import RunnableLambda
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class PairedTimeProfile:
    name: str
    absolute_deadline_utc: str
    round_minutes: int
    dispatch_minutes: int = 30

    def validate(self, *, question_count, omni_page_count=0):
        expected = {'legacy-dev24':('2026-10-10T04:25:44Z',40),
            'owner-small':('2026-10-10T07:44:02Z',40),
            'owner-40q-24page':('2026-10-10T07:44:02Z',60)}
        if (self.name not in expected or (self.absolute_deadline_utc,self.round_minutes)!=expected[self.name]
            or self.dispatch_minutes!=30 or self.name=='owner-40q-24page' and (question_count!=40 or omni_page_count!=24)
            or self.name=='legacy-dev24' and question_count!=24):
            raise ValueError('paired_owner_time_profile_invalid')

    def deadlines(self, now):
        hard=datetime.fromisoformat(self.absolute_deadline_utc.replace('Z','+00:00'))
        return hard,min(now+timedelta(minutes=self.dispatch_minutes),hard),min(now+timedelta(minutes=self.round_minutes),hard)

    def availability_error(self, now):
        return 'absolute_deadline_expired' if now >= self.deadlines(now)[0] else None


@dataclass(frozen=True)
class PairedLeaseTimeProfile(PairedTimeProfile):
    """A finite execution lease with separately configured Owner bounds.

    Never mutate this profile or a persisted Run to renew it. Operators issue a
    new native activity/lease and Run identity, preserving cumulative ledgers.
    Legacy profile dataclass fields and serialized identities remain unchanged.
    """
    lease_id: str = ''
    not_before_utc: str = ''
    owner_deadline_utc: str | None = None
    owner_not_before_utc: str | None = None

    def validate(self, *, question_count, omni_page_count=0):
        rounds={'owner-lease-small':40,'owner-lease-40q-24page':60}
        try:
            start=datetime.fromisoformat(self.not_before_utc.replace('Z','+00:00'))
            end=datetime.fromisoformat(self.absolute_deadline_utc.replace('Z','+00:00'))
            owner_start=(None if self.owner_not_before_utc is None else
                datetime.fromisoformat(self.owner_not_before_utc.replace('Z','+00:00')))
            owner_end=(None if self.owner_deadline_utc is None else
                datetime.fromisoformat(self.owner_deadline_utc.replace('Z','+00:00')))
            valid=(type(self) is PairedLeaseTimeProfile and self.name in rounds
                and self.round_minutes==rounds[self.name] and self.dispatch_minutes==30
                and (owner_start is None or owner_start.tzinfo is not None
                    and owner_start.utcoffset()==timedelta(0) and owner_start<=start)
                and (owner_end is None or owner_end.tzinfo is not None
                    and owner_end.utcoffset()==timedelta(0) and end<=owner_end)
                and type(self.lease_id) is str and re.fullmatch('[A-Za-z0-9_.:/-]{1,160}',self.lease_id)
                and start.tzinfo is not None and end.tzinfo is not None
                and start.utcoffset()==timedelta(0) and end.utcoffset()==timedelta(0) and start<end
                and type(question_count) is int and question_count>0
                and (self.name!='owner-lease-40q-24page' or (question_count==40 and omni_page_count==24)))
        except (AttributeError,TypeError,ValueError):
            valid=False
        if not valid:raise ValueError('paired_owner_time_profile_invalid')

    def availability_error(self, now):
        if self.owner_not_before_utc is not None and now<datetime.fromisoformat(self.owner_not_before_utc.replace('Z','+00:00')):
            return 'owner_window_not_started'
        if self.owner_deadline_utc is not None and now>=datetime.fromisoformat(self.owner_deadline_utc.replace('Z','+00:00')):
            return 'owner_total_deadline_expired'
        start=datetime.fromisoformat(self.not_before_utc.replace('Z','+00:00'))
        if now<start:return 'execution_lease_not_started'
        return super().availability_error(now)

    def with_native_execution_lease(self, native):
        """Read Owner policy from the injected native configuration, never guess it."""
        from dataclasses import replace
        from backend.app.application.validation_usd_budget import V6ExecutionLease
        if type(native) is not V6ExecutionLease:raise ValueError('paired_native_execution_lease_required')
        native.validate()
        if ((self.lease_id,self.not_before_utc,self.absolute_deadline_utc)!=
                (native.lease_id,native.not_before_utc,native.deadline_utc)):
            raise ValueError('paired_native_execution_lease_mismatch')
        result=replace(self,owner_deadline_utc=native.owner_deadline_utc,
            owner_not_before_utc=getattr(native,'owner_not_before_utc',None))
        result.validate(question_count=40 if self.name=='owner-lease-40q-24page' else 1,
            omni_page_count=24 if self.name=='owner-lease-40q-24page' else 0)
        return result


def time_profile(name='legacy-dev24', *, lease_id=None, lease_not_before_utc=None, lease_deadline_utc=None,
                 native_execution_lease=None):
    if name in {'owner-lease-small','owner-lease-40q-24page'}:
        if native_execution_lease is not None:
            from backend.app.application.validation_usd_budget import V6ExecutionLease
            if type(native_execution_lease) is not V6ExecutionLease:
                raise ValueError('paired_native_execution_lease_required')
            native_execution_lease.validate()
            lease_id=lease_id if lease_id is not None else native_execution_lease.lease_id
            lease_not_before_utc=lease_not_before_utc if lease_not_before_utc is not None else native_execution_lease.not_before_utc
            lease_deadline_utc=lease_deadline_utc if lease_deadline_utc is not None else native_execution_lease.deadline_utc
        profile=PairedLeaseTimeProfile(name,lease_deadline_utc,
            60 if name=='owner-lease-40q-24page' else 40,lease_id=lease_id,not_before_utc=lease_not_before_utc)
        profile.validate(question_count=40 if name=='owner-lease-40q-24page' else 1,
            omni_page_count=24 if name=='owner-lease-40q-24page' else 0)
        return profile.with_native_execution_lease(native_execution_lease) if native_execution_lease is not None else profile
    if any(value is not None for value in (lease_id,lease_not_before_utc,lease_deadline_utc,native_execution_lease)):
        raise ValueError('paired_owner_time_profile_invalid')
    if name=='legacy-dev24':return PairedTimeProfile(name,'2026-10-10T04:25:44Z',40)
    if name in {'owner-small','owner-40q-24page'}:return PairedTimeProfile(name,'2026-10-10T07:44:02Z',60 if name=='owner-40q-24page' else 40)
    raise ValueError('paired_owner_time_profile_invalid')


def measure_quickchain(chain):
    """Delegate the official three methods, measuring their actual stage boundary."""
    timing = {'generation_stage_ms': None, 'validation_stage_ms': None}
    def measure(name, function):
        def invoke(payload):
            tick = time.perf_counter()
            try:
                return function(payload)
            finally:
                timing[name] = (time.perf_counter() - tick) * 1000
        return invoke
    chain.runnable = (RunnableLambda(chain._prepare)
        | RunnableLambda(measure('generation_stage_ms', chain._generate))
        | RunnableLambda(measure('validation_stage_ms', chain._validate)))
    return timing


def latency_receipt(timing, tick, gateway):
    return {'status': 'OBSERVED_LOCAL_CLOCK', 'quickchain_end_to_end_ms': (time.perf_counter()-tick)*1000,
        **timing, 'retrieval_stage_ms': getattr(gateway, 'last_retrieval_ms', None),
        'retrieval_basis': getattr(gateway, 'last_retrieval_basis', 'NOT_OBSERVED'),
        'basis': 'official QuickChain invoke and _generate/_validate boundaries; provider latency remains usage.calls'}


def settlement_cost(*, children, facts=None, execution_kind='UNKNOWN'):
    """Forward verified native settlement facts; never price tokens or infer zero.

    Native readonly observer supplies request_id/provider/request_identity_sha256,
    execution_kind, settlement=COMPLETED, actual_charge_verified=True,
    currency=USD|CNY, actual_charge_microunits=int>=0 and source_sha256.
    Every field is matched against the durable provider child. UNKNOWN children
    remain unknown even when an incomplete observer claims a known charge.
    """
    seen = {}
    if isinstance(facts, list):
        for fact in facts:
            if isinstance(fact, dict) and isinstance(fact.get('request_id'), str):
                key = fact['request_id']
                seen[key] = fact if key not in seen else None
    calls, totals, unknown = [], {}, 0
    for child in children:
        fact = seen.get(child['request_id'])
        valid = (isinstance(fact, dict) and execution_kind in {'REAL','SIMULATED'}
            and fact.get('execution_kind') == execution_kind
            and fact.get('provider') == child['provider']
            and fact.get('request_identity_sha256') == child['request_identity_sha256']
            and child['status'] in {'succeeded','simulated','failed'}
            and fact.get('settlement') == 'COMPLETED' and fact.get('actual_charge_verified') is True
            and fact.get('currency') in {'USD','CNY'}
            and type(fact.get('actual_charge_microunits')) is int and fact['actual_charge_microunits'] >= 0
            and isinstance(fact.get('source_sha256'), str) and re.fullmatch('[0-9a-f]{64}',fact['source_sha256']))
        row = {'request_id': child['request_id'], 'status': 'AVAILABLE' if valid else 'UNKNOWN',
               'amount_microunits': None, 'currency': None, 'source': None}
        if valid:
            row.update(amount_microunits=fact['actual_charge_microunits'], currency=fact['currency'],
                       source={'kind':'NATIVE_SETTLEMENT','sha256':fact['source_sha256']})
            totals[row['currency']] = totals.get(row['currency'],0)+row['amount_microunits']
        else:
            unknown += 1
        calls.append(row)
    status = 'NOT_RUN' if not children else 'UNKNOWN' if unknown else 'AVAILABLE'
    if execution_kind == 'SIMULATED' and children:
        status = 'SIMULATED'
    return {'status':status, 'execution_kind':execution_kind, 'unknown_call_count':unknown,
        'actual_by_currency_microunits':totals if status=='AVAILABLE' else None,
        'known_observed_by_currency_microunits':totals or None, 'calls':calls,
        'source':'NATIVE_SETTLEMENT' if facts is not None else 'NOT_OBSERVED'}


def join_latest_attempt_report(results, *, state_path, run_id):
    """A new report view; never rewrite immutable case evidence or replay models."""
    from copy import deepcopy
    from eval_center.paired_state import attempts_for_qid
    view = deepcopy(results)
    for case in view:
        receipts = attempts_for_qid(state_path,run_id=run_id,qid=case['qid'])
        for arm, cell in case['arms'].items():
            receipt = receipts.get(arm,{})
            latest = (receipt.get('result') or {}).get('telemetry')
            cell['initial_telemetry'] = deepcopy(cell.get('telemetry'))
            cell['latest_telemetry'] = deepcopy(latest or cell.get('telemetry'))
            cell['association'] = {'run_id':run_id,'qid':case['qid'],'arm':arm,
                'trace_id':(cell['latest_telemetry'] or {}).get('trace_id')}
    return view


def _hotpot_normalize(answer):
    import string
    answer=''.join(c for c in answer.lower() if c not in string.punctuation)
    return ' '.join(re.sub(r'\b(a|an|the)\b',' ',answer).split())


def score_answer(prediction, references, *, dataset, metadata=None):
    from eval_center.public_runner import _answer_metrics
    metadata=metadata or {}
    if dataset=='hotpotqa':
        p=_hotpot_normalize(prediction); values=[]
        for ref in references:
            g=_hotpot_normalize(ref); a,b=p.split(),g.split()
            match=sum((Counter(a)&Counter(b)).values())
            f1=(2*match/(len(a)+len(b))) if a and b else float(a==b)
            if p!=g and (p in {'yes','no','noanswer'} or g in {'yes','no','noanswer'}):f1=0.0
            values.append((float(p==g),f1))
        return {'hotpot_answer_em':max(v[0] for v in values), 'hotpot_answer_token_f1':max(v[1] for v in values),
            'scorer':'hotpot_answer_normalization_em_token_f1', 'gold_source':'OFFICIAL_NATIVE_ANSWER',
            'supporting_facts_scoring':'SEPARATE_FROM_ANSWER'}
    if dataset=='mmlongbench-doc':
        # Native typed answers are legal Gold; no hosted GPT normalization/judge
        # has run. Preserve Not answerable and each answer_format unchanged.
        metric=_answer_metrics(prediction,references)
        return {**metric,'scorer':'PROJECT_DIAGNOSTIC_ON_OFFICIAL_TYPED_GOLD',
            'gold_source':'OFFICIAL_NATIVE_TYPED_ANSWER','answer_format':metadata.get('answer_format'),
            'official_gpt_normalized_accuracy':'NOT_RUN','official_judge':'NOT_RUN'}
    return _answer_metrics(prediction,references)


def score_retrieval_annotations(case, annotations=None):
    """Original evidence annotations, separate from document projection/QA metrics."""
    meta=case.evaluation_metadata or {}
    if annotations is None:return {'status':'NOT_RUN_SOURCE_ANNOTATION_MAPPER_REQUIRED','metrics':None}
    if 'supporting_facts' in meta:
        gold={(r['document_id'],r['sentence_index_zero_based']) for r in meta['supporting_facts']}
        predicted={(r['document_id'],i) for r in annotations for i in r.get('sentence_indices_zero_based',[])}
        hit=len(gold&predicted);precision=hit/len(predicted) if predicted else 0.0
        recall=hit/len(gold) if gold else None
        return {'status':'SCORED_RETRIEVAL_ANNOTATION_DIAGNOSTIC','gold_source':'OFFICIAL_NATIVE_SUPPORTING_FACTS',
            'metrics':{'supporting_fact_recall':recall,'supporting_fact_precision':precision,
                'supporting_fact_f1':2*precision*recall/(precision+recall) if recall is not None and precision+recall else 0.0},
            'counts':{'gold_facts':len(gold),'retrieved_facts':len(predicted)},
            'official_joint_qa_metric':'NOT_EVALUATED'}
    if 'evidence_pages' in meta:
        pages=meta['evidence_pages']
        if not pages:
            return {'status':'NOT_RUN_NO_OFFICIAL_PAGE_GOLD','metrics':None,'counts':{'gold_pages':0}}
        convention=meta.get('page_index_convention')
        proof=meta.get('page_index_proof',{})
        from eval_center.scoring_mappers import mml_page_metadata
        proven=mml_page_metadata(meta,convention_contract=proof)['page_index_convention']
        if convention not in {'ZERO_BASED','ONE_BASED'} or proven!=convention:
            return {'status':'NOT_RUN_ORIGINAL_PAGE_INDEX_CONVENTION_UNKNOWN','metrics':None}
        base=0 if convention=='ZERO_BASED' else 1
        if any(type(p) is not int or p<base for p in pages):raise ValueError('mml_gold_page_invalid')
        if len(case.qrels)!=1:raise ValueError('mml_single_source_document_required')
        document_id=next(iter(case.qrels))
        gold={(document_id,p+1-base) for p in pages}
        predicted=set()
        for row in annotations:
            if 'pages_one_based' not in row:
                return {'status':'NOT_RUN_SOURCE_PAGE_LOCATOR_UNAVAILABLE','metrics':None}
            for p in row['pages_one_based']:
                if type(p) is not int or p<1:raise ValueError('mml_prediction_page_invalid')
                predicted.add((row['document_id'],p))
        hits=len(gold&predicted);recall=hits/len(gold);precision=hits/len(predicted) if predicted else 0.0
        return {'status':'SCORED_RETRIEVAL_ANNOTATION_DIAGNOSTIC','gold_source':'OFFICIAL_NATIVE_EVIDENCE_PAGES',
            'metrics':{'evidence_page_recall':recall,'evidence_page_precision':precision,
                'evidence_page_f1':2*precision*recall/(precision+recall) if precision+recall else 0.0},
            'counts':{'gold_pages':len(gold),'retrieved_pages':len(predicted),'matched_pages':hits},
            'metric_origin':'PROJECT','page_index_proof':proof}
    return {'status':'NOT_RUN_NO_APPLICABLE_ANNOTATION_CONTRACT','metrics':None}
