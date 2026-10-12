"""Do not label a sent-but-unproven cap test as an unsent free request."""
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from scripts.probe_ordinary_output import failure_state, prior_allows_continuation, ACKNOWLEDGED_UNKNOWN, FOLLOWUP_UNKNOWN, REPAIRED_TRANSPORT_UNKNOWN


def test_proof_denial_after_response_is_sent_not_proven():
    assert failure_state(ProviderRequestNotSent('OUTPUT_CAPABILITY_EVIDENCE_INVALID'),
                         {'usage': {'completion_tokens': 1}}, {'token_settlement': 'settled'}) == 'NOT_PROVEN'


def test_observed_response_with_failed_settlement_still_remains_unknown():
    assert failure_state(ProviderUnavailable('MODEL_USAGE_SETTLEMENT_FAILED'),
                         {'usage': {'completion_tokens': 1}}, {'token_settlement': 'unknown'}) == 'UNKNOWN'


def test_uncertain_transport_remains_unknown():
    assert failure_state(ProviderUnavailable('MODEL_REQUEST_FAILED'), None) == 'UNKNOWN'


def test_explicit_pretransport_denial_is_not_sent():
    assert failure_state(ProviderRequestNotSent('MODEL_CREDENTIAL_MISSING'), None) == 'NOT_SENT'


def test_only_settled_known_failure_can_continue_without_unknown_approval():
    assert prior_allows_continuation({'state': 'NOT_PROVEN', 'provider_diagnostic': {'token_settlement': 'settled'}})
    assert not prior_allows_continuation({'state': 'NOT_PROVEN'})
    assert not prior_allows_continuation({'state': 'UNKNOWN', 'run_id': 'new-run'}, True)
    assert prior_allows_continuation({'state': 'UNKNOWN', 'run_id': ACKNOWLEDGED_UNKNOWN}, True)
    assert not prior_allows_continuation({'state': 'UNKNOWN', 'run_id': ACKNOWLEDGED_UNKNOWN})


def test_new_stop_requires_separate_exact_owner_acknowledgment():
    latest = {'state': 'UNKNOWN', 'run_id': FOLLOWUP_UNKNOWN}
    assert not prior_allows_continuation(latest, True)
    assert prior_allows_continuation(latest, True, True)
    assert not prior_allows_continuation({'state': 'UNKNOWN', 'run_id': 'another-new-unknown'}, True, True)


def test_post_repair_stop_still_needs_new_decision_and_cannot_ack_future_calls():
    latest = {'state': 'UNKNOWN', 'run_id': REPAIRED_TRANSPORT_UNKNOWN}
    assert not prior_allows_continuation(latest, True, True)
    assert prior_allows_continuation(latest, True, True, True)
    assert not prior_allows_continuation({'state': 'UNKNOWN', 'run_id': 'future-unknown'}, True, True, True)
