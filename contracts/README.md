# Contract snapshots

`openapi.json` is generated from `create_app().openapi()` with `scripts/generate_contracts.py`. `sse/events.schema.json` and `sse/events.sample.jsonl` freeze the V1 event envelope and sample sequence. `scripts/contract_test.py` compares the running application against these snapshots and rejects V2 approval, MCP, Skills, sandbox, shell, and artifact terms.
