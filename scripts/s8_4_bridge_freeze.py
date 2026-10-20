"""Freeze source-only S8.3-R inputs; expected labels remain separate."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

SELECTORS = (
    ("frost-A", "A", "df6ea66aa2b578067273ecac8c33d80bdb75f379411327d0c21e34a28dcb0306", "af69195a551d71219b33781375b75f1f7f2b3df499949cc575e49bc699329e7e"),
    ("frost-B", "B", "6ac2888ad69ab4a361855ab272db0f2114f59f5f7a3da8b13da82f5bf7c6648e", "d55864412ee3f7ee37e7f83c7f98facde079b1c83ab702dbbed3a97d97dddcd2"),
    ("leyden-negative", "B", "154227d2f3f4549245108934a6dfc8e20a15d75ddfcad850590bfec57107c6c3", "47f8f41a4df80c3435fec6ad01da8ce8c51ee2b8e66b01bf57868ffcf22f1421"),
    ("postcode-negative", "B", "c7e0fb28597260f07b2dd34c489a25fe1292a0de5f6ffe264ee8bf3cf145b83c", "0a70700694944e359a25f67416391c63d240c928283d8f7ef387607b6157c7c8"),
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def source_only(row):
    if row is None:
        return None
    row = deepcopy(row)
    # Generated descriptions and parser owner identifiers do not establish facts.
    row["knowledge_description"] = ""
    row["knowledge_custom_metadata"] = ""
    row["metadata"] = {k:v for k,v in row.get("metadata", {}).items()
        if k in ("source_version", "sha256", "index_enabled", "media_type", "chunk_count", "indexed_count")}
    return row

def freeze(raw, review=None):
    cases, proof = [], []
    for name, arm, execution, stage in SELECTORS:
        directory = raw / "executions" / execution / "evidence"
        stage_path = directory / (stage + ".stages.json")
        record = load(stage_path)
        states = {item["stage"]:item["state"] for item in record["stages"] if item.get("state")}
        search = states["retrieval"]
        rank = states.get("rerank", {})
        final = states.get("context", {})
        query, rewrite = search["query"], search["rewrite_query"]
        requests = [(p,load(p)) for p in sorted(directory.glob("*.request.json"))]
        candidates = [(p,r) for p,r in requests if isinstance(r.get("body"),dict)
            and "documents" in r["body"] and r["body"].get("query") == rewrite]
        if len(candidates) != 1:
            raise ValueError(f"{name}: expected exactly one first BGE receipt")
        request_path, request = candidates[0]
        response_path = request_path.with_name(request_path.name.replace(".request.json", ".response.body"))
        response = load(response_path)
        search_rows = search["search_results"]
        if len(search_rows) != len(request["body"]["documents"]):
            raise ValueError("REQUEST_INDEX_MAP_COUNT_MISMATCH")
        from backend.weknora_port.retrieval.types import SearchResult
        from backend.weknora_port.retrieval.rerank import enriched_passage, model_passage
        wire_bindings=[]
        for index,(row,wire) in enumerate(zip(search_rows,request["body"]["documents"])):
            item=SearchResult(**row)
            plain=enriched_passage(item)
            dedup=enriched_passage(item,deduplicate_images=True)
            views={"enriched":plain,"deduplicated":dedup,"context_header":model_passage(item,passage=dedup)}
            matched=[kind for kind,text in views.items() if text==wire]
            if not matched:
                raise ValueError(f"{name}: raw request index{index} does not match original candidate")
            wire_bindings.append({"index":index,"chunk_id":row["id"],
                "wire_sha256":hashlib.sha256(wire.encode()).hexdigest(),
                "original_text_sha256":hashlib.sha256(row["content"].encode()).hexdigest(),
                "matching_views":matched})
        score_map = [{"index":item["index"],"score":item.get("relevance_score",item.get("score"))}
            for item in response["results"]]
        ids = [r["id"] for r in search_rows]
        accepted = [ids[r["index"]] for r in score_map if r["score"] >= .3]
        trace = [
            {"stage":"rerank_raw","input_ids":ids,"response":score_map},
            {"stage":"rerank_filter_mmr","threshold":.3,"accepted_ids":accepted,
             "mmr_ids":[r["id"] for r in rank.get("rerank_results",[]) if r]},
        ]
        merge_rows = final.get("merge_results", [])
        source_rows = {}
        for row in [*search_rows, *merge_rows]:
            if row and row["chunk_type"] == "text" and not row["content_rewritten"] and not row["content_revision"]:
                source_rows.setdefault(row["id"], source_only(row))
        cases.append({
            "case_id":name, "arm":arm, "query":query,"rewrite_query":rewrite,
            "knowledge_base_ids":list(dict.fromkeys(r["knowledge_base_id"] for r in search_rows if r)),
            "document_ids":[], "rerank_threshold":.3,
            "search_results":[source_only(r) for r in search_rows],
            "rerank_results":[source_only(r) for r in rank.get("rerank_results",[])],
            "merge_results":[source_only(r) for r in merge_rows],
            "source_rows":list(source_rows.values()),"evidence_trace":trace,
        })
        proof.append({
            "case_id":name,"execution":execution,
            "artifacts":[{"path":str(p.relative_to(raw)),"sha256":sha(p)} for p in (stage_path,request_path,response_path)],
            "query":query,"rewrite":rewrite,"input_count":len(ids),
            "raw_index_to_chunk_id":ids,"request_index_bindings":wire_bindings,"raw_scores":score_map,"threshold":.3,
            "accepted_before_mmr":accepted,"normal_rerank_ids":trace[-1]["mmr_ids"],
            "normal_merge_ids":[r["id"] for r in states.get("merge",{}).get("merge_results",[]) if r],
            "final_source_ids":[r["id"] for r in merge_rows if r],
            "response_documents_echoed":any(x.get("document") is not None for x in response["results"]),
        })
    if review:
        package=load(review)
        for case in package["cases"]:
            if case["case_id"] not in ("scifact-611","mml-0687"):
                continue
            rows=[]
            for evidence in case["evidence"]:
                p=case["source_proof"][evidence["source_proof_index"]]
                rows.append(dict(id=evidence["evidence_id"],content=evidence["text"],score=.8,
                    knowledge_id=p["document_id"],knowledge_base_id=p["knowledge_base_id"],
                    chunk_type="text",chunk_index=p["chunk_index"],start_at=p["start"],end_at=p["end"],
                    metadata={"source_version":p["source_version"]},chunk_metadata={"source_version":p["source_version"]},
                    knowledge_title=p["filename"],knowledge_filename=p["filename"]))
            cases.append(dict(case_id=case["case_id"]+"-negative",arm="saved-review",query=case["question"],
                rewrite_query=case["question"],knowledge_base_ids=list(dict.fromkeys(r["knowledge_base_id"] for r in rows)),
                document_ids=[],rerank_threshold=.3,search_results=rows,rerank_results=rows,
                merge_results=rows,source_rows=rows,evidence_trace=[]))
            proof.append({"case_id":case["case_id"]+"-negative","origin":"M review source evidence only",
                "review_package_sha256":sha(review),"gold_or_answers_in_input":False})
    return {"schema":"s84a-bridge-input/v1","cases":cases}, {
        "schema":"s84a-source-proof/v1","raw_root":str(raw),"cases":proof,
        "transformations":["Generated document descriptions/custom metadata removed.",
            "Only source metadata needed for identity, version and index checks retained.",
            "Original question, text, Unicode offsets, IDs, source versions, raw scores and candidate order preserved.",
            "No Gold or historical model answers in input payload."],
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--raw-root",type=Path,required=True)
    p.add_argument("--review-package",type=Path)
    p.add_argument("--output-dir",type=Path,required=True)
    a=p.parse_args()
    inputs,proof=freeze(a.raw_root,a.review_package)
    a.output_dir.mkdir(parents=True,exist_ok=True)
    for name,data in (("inputs.json",inputs),("baseline-proof.json",proof)):
        (a.output_dir/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"status":"FROZEN","cases":len(inputs["cases"]),"gold_in_inputs":False}))

if __name__=="__main__":
    main()
