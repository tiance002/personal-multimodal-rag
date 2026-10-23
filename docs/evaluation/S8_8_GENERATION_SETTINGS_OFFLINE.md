# S8.8 Workflow A implementation evidence

Status: **IMPLEMENTED_OFFLINE_VERIFIED_REAL_PENDING_AUTH**. No Provider sends, ECS writes, commit or tag by this worker.

## Implemented contract

- `reasoning_mode=quick|smart` is separate from legacy `mode=quick`. Legacy Agent `mode=smart` remains 501. Cheap/Expensive role labels stay independent from reasoning mode.
- `max_output_tokens` accepts a strict nonnegative integer. Zero selects the finite server default. Request > saved session > source summary defaults. Default summary zero resolves Quick2048 / Smart4096; image zero resolves5000.
- Session GET/PUT `/sessions/{id}/generation-settings` uses actual `sessions.context_config.generation`, including revision. Reset clears preferences and preserves revision. New-session preferences are written in its creation transaction.
- Admission holds the session row lock while storing raw request settings and the resolved immutable snapshot in existing `port_turns.request_configuration`. Replay retains the frozen snapshot, independent of later session changes. No schema change for answer settings.
- Normal answer, fallback and legal Cheap→Expensive upgrade receive the same frozen `ChatOptions`. QU/internal generation retains its existing options. Each actual attempt writes a durable `generation_settings` event with requested/effective values, version and provider parameter mapping; complete/replay and history retain settings.
- Strict final generation validates declared finite output/context capacities, wire cap equality, actual thinking enable/disable fields, and input context bounds. Unsupported mode refuses before model send. Both local chat/vision paths validate before local ensure/pull/network access.
- Existing image bound implementation moved unchanged to `models/input_bounds.py`; `source_facts` retains its compatibility alias. Text uses conservative wire-byte bounds. Images first undergo exact identity/media validation using the unchanged reservation recipe, then a separate context recipe removes only the verified base64 payload bytes and retains the documented1024/image plus text/framing bound. Reservation semantics remain unchanged. Unknown image recipes refuse.
- Existing image GET/PATCH settings independently save mode/output cap and increment config version. UI shows null-aware Caption usage, incomplete partial text, and starts an explicit selected-asset retry with source version, path, SHA and fresh idempotency key. These are distinct from MinerU OCR settings.
- Chat UI saves/restores settings per session, accepts explicit values in the ordinary request, disables editing while submitting/running, and displays resolved values. Image settings live in a collapsible library drawer so independent document scrolling and upload position remain stable.
- Integrated root-owned shadow hooks after the existing P5 `decide`; production arguments, role and threshold remain unchanged. Integrated B-owned bounded clarification helper and explicit fixed-completion markers.

## Evidence

Exact commands, exits and limitations: `S8_8_GENERATION_SETTINGS_OFFLINE.results.json`.

- Combined affected backend modules:205 passed, exit0.
- Image bounds/accounting regression plus A settings:86 passed, exit0, permitted E: temporary evidence path.
- Real system Chrome with mocked API:27 passed, exit0. Includes save/reload, Smart→ordinary POST, frozen controls, independent image configuration, selected Caption retry and legacy UI regression.
- TypeScript/Vite build:PASS, exit0; diff whitespace:PASS, exit0.
- Opt-in `test_s88_generation_pg.py` provided for root's disposable PostgreSQL execution; local environment lacks its explicit DSN/system identity, so local actual persistence integration remains NOT RUN.
- Two legacy intent expectation failures were handed to root for baseline confirmation; their expectations were not changed. Their details are in machine results.

## Capacity and external contract limits

The audit labels capacity source `configured-model-capabilities`; passing a catalogue value is not evidence that the live Provider accepts that maximum. Unknown capacities fail closed. In particular the pinned XingChenAGI/Xing4.0-29B catalogue lookup currently has unknown output/context limits, so that binding needs real supported capacity configuration before explicit product modes may send. Existing budget/STOP/UNKNOWN gates remain effective; SiliconFlow Smart may still refuse where a verifiable total output cost bound is unavailable.

Provider switch fields were checked against primary documentation: [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) and [SiliconFlow reasoning](https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning). Actual request rendering is verified offline; no live capability or paid semantic quality result is claimed.

Final historical image capacity replay:7/7 exact saved images pass the declared context check with input upper2587–2588 plus unchanged5000 cap. Raw images range139514–2540149 bytes. Two old conservative reservation bounds exceed the model context window because they retain transport base64; the separate context recipe correctly avoids that false rejection. The old reservation bounds remain unchanged. Numeric evidence: `S8_8_HISTORICAL_IMAGE_CONTEXT_OFFLINE.json`; reproduction command in machine results.


## Actual current Cheap binding blocker — final integration finding

The actual configured Cheap identity is `siliconflow / Qwen/Qwen3.5-4B`. A direct `SourceCatalogue.resolve(ConfiguredRole(...))` check yields `context_window=0`, `max_output_tokens=0`, `reasoning=False`, `cataloged=False`. Both Quick and Smart fail with `MODEL_GENERATION_CAPACITY_UNKNOWN`, `sent=False`, before the transport opens. The focused test freezes this actual identity without fabricated capability overrides. Formal HTTP live acceptance is excluded until hosted capability proof is available.

Earlier `Qwen/Qwen3.5-397B-A17B` tests are offline request-renderer fixtures. They prove wire construction only; they do not prove this current4B binding's capacity or any model's current live catalogue availability.

The existing trusted `ConfiguredRole.parameters.spec_override` path can carry supported metadata, but no override was applied. [SiliconFlow's official family announcement](https://www.siliconflow.cn/news/yg6n19y2g6frnp4koxu8ye48) supports the256K context statement. [Official chat documentation](https://docs.siliconflow.cn/docs/api/chat-completions-post) says `max_tokens` excludes reasoning; it does not establish this hosted4B model's output maximum. Historical requests demonstrate a past accepted2048/off point, which is not a hosted maximum or Smart total bound. No capacity ceiling, production binding, threshold or unknown-capacity guard was changed.

## Root integration follow-up

The worker-local NOT_RUN entries above are retained as execution history. Root subsequently ran both targeted PG tests in the final isolated image: exit0, 2 passed (Session settings and selected image retry/cancellation). The disposable cluster was rolled back and removed; production volumes were untouched. Two old intent fixture failures were independently reproduced on baseline f3a541 with unchanged expectations. See S8_8_IMPLEMENTATION_AND_ACCEPTANCE.md and pg-final-image.log. New business Provider calls remain0.
