# Estimator receipt byte binding addendum, 2026-10-15

No production estimator change. Separate evidence-only scope from P8 and reference fix4488ac1. Old SERIAL_INTEGRATION receipt and original253/27/8 results are preserved byte-for-byte. No blanket old Git-byte binding PASS is asserted.

## Identified exact byte form

The old receipt.production_sha256[backend/weknora_port/generation/estimator.py] is31d5156f6b117eb065d3410e3947f16717036b0a7b973a07b0992797dda88fef. The current preserved working-tree file has exactly those bytes:2630bytes, one CR at offset2628, immediately before its final LF. All preceding51newlines are LF. The earlier review checked uniform LF and uniform CRLF (2681bytes); neither represents these mixed-end-of-line bytes.

The accepted53f4b87 Git blob fdc263609cd6e5924fa66783bd89134f479b5537 is2629bytes, SHA2563db5a02df3d6e7a235f1e219a5d204d5b921148d0c6799b50403d7733030b291. Exact reconstruction is git_blob[:-1] + b'\r\n'. This matches the old receipt's digest exactly. Both byte forms produce identical Python ASTs. Prior P8 proof compares53f4b87/ec98695/9567c88 Git blobs; all equal. Latest estimator source commit remains1a045b979a52f6ebae4dc2b611c34ec556ca5023.

The mismatch was comparing a working-file raw-byte receipt with Git's normalized text byte form, specifically a single final CRLF, not uniform checkout conversion. This identifies the precise digest input, not an authenticated historical process timeline. When/how that final newline was introduced is UNKNOWN; no restricted session logs accessed, no invented edit chronology. Disk observation and old matching digest support byte provenance; source AST does not show an algorithm change.

## Preserved artifacts and fresh affected checks

Directory docs/weknora-port/evidence/ESTIMATOR_RECEIPT_BINDING_20261015/:

- estimator.receipt.raw: exact archived2630bytes matching original expected digest.
- estimator.git.raw: exact canonical2629bytes read from53f4b87, not reconstructed source text.
- binding.json: raw/canonical hashes, old receipt hash, CR offset and exact reconstruction/AST checks.
- bound_verify.py: explicitly loads one archived source form under the production estimator module name, then runs only tests/weknora_port/test_estimator.py (real local pinned tiktoken asset; no provider/network).
- receipt.test-binding.json and git.test-binding.json bind actual loaded __file__, source SHA, test SHA and exits.

Commands, with PYTHONPATH pointing to current project and existing reader/runtime-deps:

`E:/RAG quention/.venv/Scripts/python.exe -B docs/weknora-port/evidence/ESTIMATOR_RECEIPT_BINDING_20261015/bound_verify.py receipt`: exit0,1 passed0.46s.

`E:/RAG quention/.venv/Scripts/python.exe -B docs/weknora-port/evidence/ESTIMATOR_RECEIPT_BINDING_20261015/bound_verify.py git`: exit0,1 passed0.20s.

Both paths exercise local BPE, full message tokenization including literal special-token text, longer-message contribution and unsupported input/missing/bad asset UNKNOWN boundaries. They are fresh narrowly scoped checks, not a substitute/relabel of historical253/27/8 or full same-input differential testing. No rerun of those old whole suites. One pytest plugin import-order warning retained; no test skip/failure. Real provider calls0.

Old receipt SHA256 remains a9796d6056f33c38d177387d7679cac6b170a75346d46f1837af284a8dff4f33. Parent reviewer can compare archived raw bytes directly to its original expected digest, then normalize/reconstruct to compare Git. This addendum supplies missing raw-byte evidence without rewriting original claims. Independent acceptance pending; no REAL_PROVIDER/CUTOVER/full parity claim.
