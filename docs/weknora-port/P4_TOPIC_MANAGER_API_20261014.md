# Source topic manager native APIs

Fixed handler/memory.go:ListTopics/PromoteTopic/DeleteTopic and memory/service.go:ListTopics/PromoteTopic/DeleteTopic now have native GET /memory/topics, POST /memory/topics/{id}/promote and DELETE /memory/topics/{id}. List uses exact unpromoted filter and hits DESC,last_seen_at DESC,50default/200cap HTTP paging. EffectiveInterestThreshold follows source default3/max20. Topic view includes ID/topic/aliases/hits/threshold/last_seen_at, excludes tenant/subject. Promotion/deletion delegate existing scoped TopicService/MemoryManager; no new model/ranking policy.

Actual command: `E:/RAG quention/.venv/Scripts/python.exe -B -m pytest tests/weknora_port/test_memory_topics_api_pg.py -q --junitxml=docs/weknora-port/evidence/P4_MEMORY_TOPICS_API_20261014.junit.xml`, exit0;1passed1.48s. Actual FastAPI ASGI + dedicated PG verifies paging/count/view shape, clamp20, promotion interest and removal from unpromoted list, repeated promotion404, canonical/alias tombstones, foreign-owner403 and zero model sends. Synthetic owner only; originals/historical memory untouched.

Bounded native API ISOLATED_INTEGRATION_PASS. Existing TopicService.promote mark-promoted failure handling still raises where source warns; fault branch not yet compared. Document-affinity and remaining clear/export/manual item routes, full source worker/Asynq behavior and UI topic management remain open. No wholeP4 or real/cutover acceptance.


Follow-up source fault branch closed: TopicService.promote now warns locally and returns the successfully written interest when mark_topic_promoted fails, matching memory/service.go:PromoteTopic. Actual PG/API controlled-mark failure plus existing scope/paging/tombstone assertions: P4_MEMORY_TOPICS_MARK_FAILURE_20261014,1passed1.49s exit0. Created item is active, counter remains unpromoted, no model sends. Earlier pending wording for this fault branch is superseded; other listed gaps remain.
