"""Frozen historical probe predicates only: no CLI, credentials, transport or writes.

Source: scripts/probe_ordinary_output.py at 18ac530; retained to verify
UNKNOWN accounting without retaining the obsolete real-send entry.
"""

ACKNOWLEDGED_UNKNOWN = '046c3327-5dd9-4d44-a6e6-876897da41ab'

FOLLOWUP_UNKNOWN = '72917fff-bd37-4607-b3db-7c7e402d3fbf'

REPAIRED_TRANSPORT_UNKNOWN = '33a016bc-41d1-4340-a072-5dd50b594af1'

def failure_state(error, known_response, provider_receipt=None):
    from backend.app.ports.providers import ProviderRequestNotSent
    if known_response is not None:
        return 'NOT_PROVEN' if provider_receipt and provider_receipt.get('token_settlement') == 'settled' else 'UNKNOWN'
    return 'NOT_SENT' if isinstance(error, ProviderRequestNotSent) else 'UNKNOWN'

def prior_allows_continuation(row, acknowledged_unknown=False, acknowledge_followup=False,
                             acknowledge_repaired_transport=False):
    if row['state'] in {'VERIFIED', 'NOT_SENT'}:
        return True
    if row['state'] == 'NOT_PROVEN':
        return row.get('provider_diagnostic', {}).get('token_settlement') == 'settled'
    return bool(row['state'] == 'UNKNOWN' and (
        acknowledged_unknown and row.get('run_id') == ACKNOWLEDGED_UNKNOWN or
        acknowledge_followup and row.get('run_id') == FOLLOWUP_UNKNOWN or
        acknowledge_repaired_transport and row.get('run_id') == REPAIRED_TRANSPORT_UNKNOWN))
