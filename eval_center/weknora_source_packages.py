"""Frozen file-transport originals for new-core ingestion, never legacy execution."""
from __future__ import annotations
import argparse, hashlib, json
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath

OWNER_MANIFESTS = {
    "hotpotqa": "9677d23be67da34bc72fa09d031cea698f2011408ccf5b46928838e4736a9202",
    "mmlongbench-doc": "cd949b08ba2667461b64e9955b83f1d35d0950f7ce8f16905f315deb60aff87f",
    "omnidocbench": "a36566301fea02e966c28baeb9a7cc44c5c16c13bf5f3423a654d681ff75c41a",
}
OWNER_INDEX = "35f832c971abdde2b71c74102d8070d0cbd560d745da73c3e8d782a5753b48fc"

@dataclass(frozen=True)
class SourceAsset:
    dataset: str
    unit_id: str
    source_document_id: str
    filename: str
    media_type: str
    path: str
    sha256: str
    size: int
    source_revision: str

    def read(self) -> bytes:
        return checked_bytes(Path(self.path), self.sha256, self.size)


def checked_bytes(path, sha256, size=None):
    if not isinstance(sha256,str) or len(sha256)!=64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("SOURCE_SHA256_REQUIRED")
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha256 or (size is not None and len(raw)!=size):
        raise ValueError("SOURCE_BYTES_IDENTITY_MISMATCH")
    return raw


def load_originals(manifest_path, expected_sha256, dataset):
    path=Path(manifest_path).resolve();root=path.parent
    manifest=json.loads(checked_bytes(path,expected_sha256))
    if manifest.get("schema")!="rag-v6-file-transport/v1" or manifest.get("dataset")!=dataset:
        raise ValueError("SOURCE_PACKAGE_IDENTITY_MISMATCH")
    if manifest.get("Gold_qrels_answers_included") is not False:
        raise ValueError("SOURCE_GOLD_BOUNDARY_UNKNOWN")
    assets=[];seen=set()
    allowed={"hotpotqa":("text/plain",),"mmlongbench-doc":("application/pdf",),"omnidocbench":("image/jpeg","image/png")}
    for unit in manifest["units"]:
        source=unit["source"];ref=source["artifact"];relative=PurePosixPath(ref["path"])
        # Exclusively original object refs: never prepared/chunks/QA/Gold paths.
        if relative.is_absolute() or relative.parts!=("objects",ref["sha256"]) or "\\" in ref["path"]:
            raise ValueError("SOURCE_OBJECT_REF_INVALID")
        obj=(root/Path(*relative.parts)).resolve()
        if not obj.is_relative_to(root):raise ValueError("SOURCE_OBJECT_REF_ESCAPES_PACKAGE")
        filename=source["file_name"]
        if Path(filename).name!=filename or "/" in filename or "\\" in filename or not filename:
            raise ValueError("SOURCE_FILENAME_INVALID")
        if source["media_type"] not in allowed[dataset]:raise ValueError("SOURCE_MEDIA_TYPE_MISMATCH")
        if unit["unit_id"] in seen:raise ValueError("SOURCE_UNIT_DUPLICATE")
        seen.add(unit["unit_id"])
        checked_bytes(obj,ref["sha256"],ref["bytes"])
        assets.append(SourceAsset(dataset,unit["unit_id"],source["source_document_id"],filename,
            source["media_type"],str(obj),ref["sha256"],ref["bytes"],source["provenance"]["source_revision"]))
    return tuple(assets)


def inventory(root):
    root=Path(root)
    checked_bytes(root/"six-package-manifest.json",OWNER_INDEX)
    rows=[]
    for dataset,sha in OWNER_MANIFESTS.items():
        assets=load_originals(root/"packages"/dataset/"manifest.json",sha,dataset)
        rows.append({"dataset":dataset,"manifest_sha256":sha,"source_assets":[asdict(a) for a in assets],
            "source_status":"SOURCE_ASSETS_VERIFIED","source_units":len(assets),
            "qa_status":"BLOCKED_INDEPENDENT_PREMODEL_QA_REQUIRED" if dataset=="omnidocbench" else "BLOCKED_SELECTED_NATIVE_QA_GOLD_NOT_PROVIDED",
            "official_qa_count":0 if dataset=="omnidocbench" else None,
            "answer_accuracy":None,"full_chain_status":"NOT_RUN"})
    return {"schema":"weknora-source-originals-v1","datasets":rows,"models_called":0,
        "legacy_grants_loaded":False,"prepared_v6_chunks_used":False,"gold_indexed":False,
        "p7_full_chain_status":"NOT_RUN"}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--package-root",required=True);parser.add_argument("--report",required=True)
    args=parser.parse_args();result=inventory(args.package_root)
    Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"source_units":{r["dataset"]:r["source_units"] for r in result["datasets"]},"qa":"BLOCKED","p7":"NOT_RUN"}))
    return 0

if __name__=="__main__":raise SystemExit(main())
