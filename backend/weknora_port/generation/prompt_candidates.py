"""System-owned candidate; opt-in at evaluation composition, never a UI prompt."""
import hashlib

CANDIDATE_VERSION = 's88-source-precision/v1'
CANDIDATE_RULES = (
    'Keep procedures from different sources separate when their steps are incompatible; preserve each procedure\'s conditions and exceptions.\n'
    'Make the opening conclusion agree with the final explanation. Bind each date to its specific event, such as founding, renaming, or merger.\n'
    'Distinguish conflicting sources explicitly; do not resolve a conflict by inventing identifiers, aliases, or explanations.\n'
    'People and cases described in sources are not the current user; do not attribute their experiences to the user.'
)
_MARKER = '\n\nSystem answer precision [' + CANDIDATE_VERSION + ']:\n'


def apply_candidate(system_prompt, *, version=CANDIDATE_VERSION):
    if version == 'off':
        return system_prompt
    if version != CANDIDATE_VERSION:
        raise ValueError('SYSTEM_PROMPT_CANDIDATE_VERSION_INVALID')
    if 'System answer precision [' in system_prompt:
        raise ValueError('SYSTEM_PROMPT_CANDIDATE_ALREADY_APPLIED')
    return system_prompt + _MARKER + CANDIDATE_RULES


def candidate_manifest(system_prompt):
    candidate = apply_candidate(system_prompt)
    return {'version': CANDIDATE_VERSION,
            'base_sha256': hashlib.sha256(system_prompt.encode()).hexdigest(),
            'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
            'added_utf8_bytes': len(candidate.encode()) - len(system_prompt.encode()),
            'real_quality_status': 'PAIRED_EVAL_PENDING_AUTH',
            'default_changed': False}
