"""Six-case offline Top1 policy replay; saved model scores, zero network sends."""
from __future__ import annotations
import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict, fields
import hashlib
import html
import importlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "78bf9839d2030749f715a9395ce355209d8f52ff"
from backend.weknora_port.models.types import CallContext, RerankResponse, RerankResult, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchNothing, SearchTarget
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from scripts.rag_m32_threshold_replay import SavedRepository


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def textsha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def norm(text):
    return "".join(html.unescape(text).split())


def blocked(*args, **kwargs):
    raise RuntimeError("OFFLINE_NETWORK_FORBIDDEN")


def baseline_module():
    raw = subprocess.check_output(["git", "show", BASE + ":backend/weknora_port/retrieval/rerank.py"], cwd=ROOT)
    module = ModuleType("backend.weknora_port.retrieval.frozen_top1_baseline")
    module.__package__ = "backend.weknora_port.retrieval"
    exec(compile(raw, "S5_GIT_BLOB:rerank.py", "exec"), module.__dict__)
    return module, hashlib.sha256(raw).hexdigest()


def result_rows(values):
    return [SearchResult(**{k: v for k, v in r.items() if k in {f.name for f in fields(SearchResult)}})
            for r in values]


def selection(rows):
    return [dict(id=r.id, document_id=r.knowledge_id, kind=r.chunk_type,
                 fused_score=r.score, chars=len(r.content), sub_chunk_ids=r.sub_chunk_ids.copy(),
                 source_version=r.chunk_metadata.get("source_version") or r.metadata.get("source_version"),
                 content=r.content, content_sha256=textsha(r.content)) for r in rows]


def load_cases(args, record):
    p33 = args.p33
    stage = record(p33 / "question/evidence/a56283885a369eb4bbfef4b0e3373f3a5e453c6f39a37cefea2c4c21f9763857.stages.json")
    captured = next(x["state"] for x in stage["stages"] if x["stage"] == "retrieval" and x["status"] == "completed")
    wire = record(p33 / "question/evidence/b4cc9b77c3c0406c85aa3d961322c6c1.request.json")
    response = record(p33 / "question/evidence/b4cc9b77c3c0406c85aa3d961322c6c1.response.body")
    upload = record(p33 / "upload/source-and-index.json")
    p34 = next(r for r in upload["chunks"] if r["id"] == "956cf096-d183-4a96-adf5-75e9fd71a4e2")
    candidates = result_rows(captured["search_results"])
    cases = [dict(case_id="mml-0434-page33", question=captured["query"], query=wire["body"]["query"],
        kb_id=candidates[0].knowledge_base_id, rows=candidates, wire=wire, response=response,
        with_context=True, topk=30, owner=wire["scope"]["owner_id"],
        topk_provenance="Saved run used normal runtime defaults; top30 from S5 ConversationSettings, no explicit override in its launcher.",
        target_id="684b1220-4546-4235-8b18-53b7d9be3baa", secondary_id=p34["id"],
        quotes=[dict(id="coding_time_image_text", text="Coding time!", role="literal_page33_title"),
                dict(id="dataset_page34", text=p34["content"], role="real_neighbor_source_not_Search_hit")],
        construction=None, history_status="New saved session; no history references injected in replay.")]
    plan = record(args.s1 / "plan.json")
    native = record(args.s1 / "native_analysis.json")
    quotes = record(args.quote_plan)
    targets = {"hotpot-frost": "31417c7c-7abf-4723-956a-74bf7ee14683",
               "hotpot-romeo": "fb294950-cd77-4da1-ada6-b2c76aaa07da",
               "mml-0959": "df6098cb-68e6-4546-b9e0-8682b4bf19e9",
               "omni-page007": "51316a20-67fd-4732-b520-c049999933d0"}
    for name in ("hotpot-frost", "hotpot-romeo", "mml-0959", "omni-page007", "omni-no-exact-fact"):
        case = next(c for c in plan["cases"] if c["case_id"] == name)
        folder = args.s1 / ("live-" + name)
        inputs = record(folder / "inputs.json")
        actual_wire_paths = [p for p in (folder / "evidence").glob("*.request.json")
                             if read(p).get("body", {}).get("documents") == inputs["A"]]
        assert len(actual_wire_paths) == 1, "FROZEN_A_WIRE_NOT_UNIQUE"
        actual_wire = record(actual_wire_paths[0])
        raw_response = record(actual_wire_paths[0].with_name(actual_wire_paths[0].name.replace(".request.json", ".response.body")))
        adapted = record(folder / "A.response.json")
        assert [(r["index"], r["relevance_score"]) for r in raw_response["results"]] == [
            (r["index"], r["score"]) for r in adapted["results"]]
        actual_native = next(c for c in native["cases"] if c["case_id"] == name)
        qid = case["source_input_identity"].get("original_qid", "")
        matched = next((c for c in quotes["cases"] if qid and qid in c["case_id"]), {})
        q = [dict(id=x["id"], text=x["text"], role=x["fact_scope"]) for x in matched.get("quotes", [])]
        if name == "mml-0959":
            q = [dict(id="radio_long_distance", text="ionosphere refracts radiowaves", role="literal_answer_fact"),
                 dict(id="microwave_long_distance", text="microwaves can be used to send signals to satellites", role="literal_answer_fact")]
        if name == "omni-page007":
            target = next(r for r in inputs["candidates"] if r["id"] == targets[name])
            q = [dict(id="exact_old_ocr_body", text=target["content"], role="exact_raw_OCR_delivery_not_matrix_correctness")]
        cases.append(dict(case_id=name, question=case["question"], query=inputs["query"], kb_id=case["kb_id"],
            rows=result_rows(inputs["candidates"]), wire=actual_wire, response=raw_response,
            with_context=False, topk=native["settings"]["rerank_top_k"],
            topk_provenance="Existing S1 native replay's fixed topk (5); both policy arms identical.",
            owner=actual_wire["scope"]["owner_id"], target_id=targets.get(name), quotes=q,
            historical_native_ids=actual_native["arms"]["A"]["final_ids"],
            construction=case.get("construction"), history_status="Direct saved Rerank diagnostic, empty history frozen in S1 native replay."))
    return cases


async def arm(case, module, repo, context):
    state = PipelineState(query=case["question"], rewrite_query=case["query"],
        targets=(SearchTarget(case["kb_id"]),), search_results=deepcopy(case["rows"]),
        rerank_model_id="SAVED_RESPONSE_ONLY", rerank_threshold=.3, rerank_top_k=case["topk"],
        rerank_context_enabled=case["with_context"], source_evidence_mode="off")
    observed, calls = [], 0
    response = case["response"]["results"]
    class SavedResponse:
        async def rerank(self, query, documents, ctx):
            nonlocal calls
            calls += 1
            assert query == case["wire"]["body"]["query"] and documents == case["wire"]["body"]["documents"]
            return RerankResponse(tuple(RerankResult(r["index"], r["relevance_score"]) for r in response))
    class Registry:
        async def rerank(self, *unused):
            return SavedResponse()
    def observe(frame, event, arg):
        if event == "call" and frame.f_code is module.mmr.__code__:
            observed.extend(deepcopy(frame.f_locals["candidates"]))
    out = dict(status="OFFLINE_REPLAY", accepted_pre_mmr=[], after_mmr=[], after_merge=[], after_topk=[],
               context="", generation_user_message="", context_chars=0, generation_chars=0)
    segmenter = default_segmenter()
    previous = sys.getprofile()
    try:
        try:
            sys.setprofile(observe)
            await module.rerank(state, context, Registry(), segmenter)
        finally:
            sys.setprofile(previous)
        out["accepted_pre_mmr"] = selection(observed)
        out["after_mmr"] = selection(state.rerank_results)
        await merge(state, context, repo, segmenter)
        out["after_merge"] = selection(state.merge_results)
        filter_top_k(state)
        out["after_topk"] = selection(state.merge_results)
        final = await into_chat_message(state, context)
        prepared = prepare_generation(GenerationRequest("offline", "offline", "offline", case["question"],
            final, tuple(Evidence.from_result(r) for r in state.merge_results)))
        text = html.unescape(prepared.messages[-1].content)
        out.update(context=final.rendered_contexts, generation_user_message=text,
            final_ids=list(final.result_ids), context_chars=len(final.rendered_contexts), generation_chars=len(text))
    except SearchNothing:
        out.update(status="SEARCH_NOTHING", final_ids=[])
    except PermissionError as error:
        out.update(status="PARTIAL_SOURCE_NOT_REPLAYABLE", failure_code=str(error), final_ids=[])
    out["saved_response_playbacks"] = calls
    out["source_rows_requested"] = sorted(repo.requested)
    out["quotes"] = [dict(q, search=any(norm(q["text"]) in norm(r.content) for r in case["rows"]),
        rerank=any(norm(q["text"]) in norm(r["content"]) for r in out["after_mmr"]),
        context=norm(q["text"]) in norm(out["generation_user_message"]),
        scope="literal text delivery; not source-relation or answer-entailment verdict") for q in case["quotes"]]
    out["known_distractors_in_context"] = [q["id"] for q in out["quotes"] if q["role"] == "KNOWN_DISTRACTOR" and q["context"]]
    if case["case_id"] == "omni-no-exact-fact":
        out["fact_status"] = "FACT_MISSING"
        out["known_topic_only_sources_in_context"] = [r["id"] for r in out["after_topk"]]
    else:
        relevant = [q for q in out["quotes"] if q["role"] != "KNOWN_DISTRACTOR"]
        out["fact_status"] = "FACT_PRESENT" if relevant and all(q["context"] for q in relevant) else (
            "FACT_PARTIAL" if any(q["context"] for q in relevant) else "FACT_MISSING")
        out["known_topic_only_sources_in_context"] = []
    out["unreviewed_relevance_count"] = "UNKNOWN: no exhaustive candidate/neighbor relevance labels"
    out["fact_status_scope"] = "Presence of separately reviewed literal source quotes, not complete answer entailment"
    if case["case_id"] == "mml-0434-page33":
        out["coding_time_to_dataset_relation"] = "UNVERIFIED: adjacency is not chapter membership"
    out["answer_quality_after_policy_change"] = "NOT_RUN_NO_GENERATION"
    return out


async def main(args):
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    provenance = {}
    def record(path):
        provenance[str(path)] = digest(path)
        return read(path)
    a, a_sha = baseline_module()
    b = importlib.import_module("backend.weknora_port.retrieval.rerank")
    cases = load_cases(args, record)
    snapshot = record(args.snapshot)
    extra = record(args.extra_snapshot)
    source_rows = {r.id: r for r in result_rows(snapshot["rows"] + extra["rows"])}
    results = []
    for case in cases:
        for candidate in case["rows"]:
            source = source_rows[candidate.id]
            assert (source.content, source.knowledge_id, source.knowledge_base_id, source.chunk_type) == (
                candidate.content, candidate.knowledge_id, candidate.knowledge_base_id, candidate.chunk_type)
            assert source.chunk_metadata.get("source_version") == candidate.chunk_metadata.get("source_version")
            assert all(getattr(source, field) == getattr(candidate, field) for field in (
                "parent_chunk_id", "pre_chunk_id", "next_chunk_id", "chunk_index", "start_at", "end_at")), "SAVED_SOURCE_LINK_OR_OFFSET_CHANGED"
        expected = [b.model_passage(r) if case["with_context"] else b.enriched_passage(r) for r in case["rows"]]
        assert expected == case["wire"]["body"]["documents"], "EXACT_PASSAGE_RECONSTRUCTION_FAILED"
        context = CallContext("OFFLINE_TOP1", TrustedScope(case["owner"], (case["kb_id"],), ()))
        raw = [dict(provider_rank=i + 1, input_index=r["index"], chunk_id=case["rows"][r["index"]].id,
            provider_raw_score=r["relevance_score"], search_score=case["rows"][r["index"]].score)
            for i, r in enumerate(case["response"]["results"])]
        arms = {label: await arm(case, module, SavedRepository(source_rows), context)
                for label, module in (("A", a), ("B", b))}
        for label, output in arms.items():
            output["target_transitions"] = {stage: any(r["id"] == case.get("target_id") or case.get("target_id") in r["sub_chunk_ids"]
                for r in output[stage]) for stage in ("accepted_pre_mmr", "after_mmr", "after_merge", "after_topk")}
        results.append(dict(case_id=case["case_id"], original_question=case["question"], actual_query=case["query"],
            kb_id=case["kb_id"], search_count=len(case["rows"]), candidate_ids_in_order=[r.id for r in case["rows"]],
            candidate_contents=[dict(id=r.id, document_id=r.knowledge_id, kind=r.chunk_type,
                source_version=r.chunk_metadata.get("source_version"), text=r.content, sha256=textsha(r.content)) for r in case["rows"]],
            raw_scores=raw, highest_score=max(raw, key=lambda r:r["provider_raw_score"]) if raw else None,
            target_id=case.get("target_id"), target_raw=next((r for r in raw if r["chunk_id"]==case.get("target_id")), None),
            secondary_id=case.get("secondary_id"), construction=case.get("construction"),
            with_context=case["with_context"], topk=case["topk"], topk_provenance=case["topk_provenance"],
            history_status=case["history_status"], fixed_input_wire_sha256=textsha(json.dumps(case["wire"]["body"], ensure_ascii=False, sort_keys=True)),
            arms=arms, C={"status":"NOT_DEFINED","reason":"Owner supplied no C strategy; no third strategy invented."},
            existing_S1_A_final_ids_match=arms["A"]["final_ids"] == case["historical_native_ids"] if "historical_native_ids" in case else "NOT_APPLICABLE_SAVED_P33_RERANK_SEARCH_NOTHING"))
        print(case["case_id"], [(key, val["status"], len(val["after_topk"]), val["context_chars"]) for key,val in arms.items()])
    result = dict(schema="rerank-top1-fallback-ab/v1", mode="OFFLINE_SAVED_REAL_RESPONSES_CURRENT_PRODUCT_FUNCTIONS",
        baseline_commit=BASE, experiment_branch=subprocess.check_output(["git","branch","--show-current"],cwd=ROOT).decode().strip(),
        status="COMPLETE_OFFLINE_REPLAY" if all(x["arms"][label]["status"] != "PARTIAL_SOURCE_NOT_REPLAYABLE" for x in results for label in ("A","B")) else "PARTIAL",
        decision="KEEP_EXPERIMENTAL", A={"threshold":.3,"top1_minimum":.15},
        B={"threshold":.3,"top1_minimum":None,"when":"empty threshold selection after successful valid response only"},
        C={"status":"NOT_DEFINED"}, cases=results,
        baseline_module_sha256=a_sha, source_hashes=provenance,
        changed_source_sha256={str(p.relative_to(ROOT)):digest(p) for p in (Path(__file__),ROOT/'backend/weknora_port/retrieval/rerank.py')},
        summary=dict(cases=6, completed_case_policy_replays=12,
            search_nothing={label:sum(x['arms'][label]['status']=='SEARCH_NOTHING' for x in results) for label in ('A','B')},
            context_character_totals={label:sum(x['arms'][label]['context_chars'] for x in results) for label in ('A','B')},
            literal_reviewed_quote_delivery={label:dict(present=sum(q['context'] for x in results for q in x['arms'][label]['quotes'] if q['role']!='KNOWN_DISTRACTOR'),
                denominator=sum(q['role']!='KNOWN_DISTRACTOR' for x in results for q in x['arms'][label]['quotes'])) for label in ('A','B')},
            known_distractor_occurrences={label:sum(len(x['arms'][label]['known_distractors_in_context']) for x in results) for label in ('A','B')}),
        new_provider_calls=0, new_provider_tokens=0, new_generation_calls=0, database_writes=0,
        new_unknown=0, historical_unknown_modified=False,
        limitations=["No new Rerank or generation; saved scores apply only to their exact original ordered passage inputs.",
            "No full relevance labels: unreviewed sources remain UNKNOWN, not noise=0.",
            "The constructed no-answer response already has Top1 above .3, so it does not exercise newly admitted low-score negative fallback risk.",
            "0434 neighbor text may be delivered by unchanged Merge, but that is not a Search hit or proof of Coding-time chapter membership.",
            "Omni old OCR quote delivery does not validate matrix positions/signs; no answer generation performed.",
            "Romeo official support/source completeness is separately listed; not every annotated support sentence is proven necessary to answer the director question.",
            "Old source snapshot is not a historical database snapshot; strict candidate identity/body/version comparisons precede replay."])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=result['status'],decision=result['decision'],summary=result['summary'],output=str(args.output))))
    if result['status'] != 'COMPLETE_OFFLINE_REPLAY':
        raise SystemExit(2)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p33',type=Path,default=Path(r'E:\WK\run\s1-0434-section-20261019\real-check'))
    parser.add_argument('--s1',type=Path,default=Path(r'E:\WK\run\s1-rerank-input-20261019'))
    parser.add_argument('--snapshot',type=Path,default=Path(r'E:\WK\run\rag-m32-20261018\replay-source-snapshot.json'))
    parser.add_argument('--extra-snapshot',type=Path,required=True)
    parser.add_argument('--quote-plan',type=Path,default=Path(r'E:\WK\run\m4-s1-20261018\replay_plan.json'))
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
