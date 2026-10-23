"""System-owned candidate; opt-in at evaluation composition, never a UI prompt."""
import hashlib
from types import MappingProxyType

CANDIDATE_VERSION = 's88-source-precision/v1'
CANDIDATE_RULES = (
    'Keep procedures from different sources separate when their steps are incompatible; preserve each procedure\'s conditions and exceptions.\n'
    'Make the opening conclusion agree with the final explanation. Bind each date to its specific event, such as founding, renaming, or merger.\n'
    'Distinguish conflicting sources explicitly; do not resolve a conflict by inventing identifiers, aliases, or explanations.\n'
    'People and cases described in sources are not the current user; do not attribute their experiences to the user.'
)
_MARKER = '\n\nSystem answer precision [' + CANDIDATE_VERSION + ']:\n'


# Exact evaluated suffix text, beginning with the marker (the two separating
# newlines belong to apply_candidate). Preserve version-specific final newlines.
# Catalog values and hashes are immutable; selecting a version remains opt-in.
CANDIDATE_SUFFIXES = MappingProxyType({
    CANDIDATE_VERSION: _MARKER[2:] + CANDIDATE_RULES,
    's88-source-precision/v2': (
        'System answer precision [s88-source-precision/v2]:\n'
        'Answer the specific question from the supplied evidence, putting the core answer first. Omit optional claims that do not help answer it.\n'
        'For procedures, choose a coherent source-supported method and preserve its prerequisites, order, timing and do/not-do conditions. Label alternative methods separately; never combine incompatible steps.\n'
        'For requested lists, collect the relevant distinct items before expanding descriptions; do not claim an exhaustive list unless the supplied evidence supports that coverage.\n'
        'Keep entities, dates, units and events paired with their source. Distinguish establishment from renaming, and stated facts from questions, opinions or examples. Source cases are not facts about the current user.\n'
        'State supported alternatives when sources conflict. If the target or evidence is insufficient, identify the specific gap; do not guess a value, URL suffix, relationship, or zero from absence.\n'
        'Cite a claim only with the current source that actually supports it, using the existing citation syntax.'
    ),
    's88-source-precision/v3': (
        'System answer precision [s88-source-precision/v3]:\n'
        'Answer the specific question from the supplied evidence, putting the core answer first. Omit optional claims that do not help answer it.\n'
        'For procedures, choose a coherent source-supported method and preserve its prerequisites, order, timing and do/not-do conditions. Label alternative methods separately; never combine incompatible steps.\n'
        'For requested lists, check all directly relevant source passages for distinct items within the requested scope, including items mentioned only once. Deduplicate aliases, then give a compact list before optional descriptions. Do not silently narrow a broad request to the best-known examples. An uncertain attribute is not a reason to omit an otherwise supported item: mark that attribute as unspecified or conflicting. Exclude incidental examples outside the requested scope; do not claim completeness beyond the supplied evidence.\n'
        "Bind every date to the entity and event explicitly described by its source. Answer the event asked about, preserving the source's event wording. A later name change is not evidence of a new establishment: distinguish the establishment of the continuing entity from adoption of its later name. If the intended event is ambiguous, state the supported events separately rather than substituting one for the other. Check that the opening answer and any subsequent explanation make the same claim.\n"
        'Keep entities, units and events paired with their source. Distinguish stated facts from questions, opinions or examples. Source cases are not facts about the current user.\n'
        'State supported alternatives when sources conflict. If the target or evidence is insufficient, identify the specific gap; do not guess a value, URL suffix, relationship, or zero from absence.\n'
        'Cite a claim only with the current source that actually supports it, using the existing citation syntax.\n'
    ),
})
CANDIDATE_SUFFIX_SHA256 = MappingProxyType({
    version: hashlib.sha256(suffix.encode()).hexdigest()
    for version, suffix in CANDIDATE_SUFFIXES.items()
})


def apply_candidate(system_prompt, *, version=CANDIDATE_VERSION):
    if version == 'off':
        return system_prompt
    if version not in CANDIDATE_SUFFIXES:
        raise ValueError('SYSTEM_PROMPT_CANDIDATE_VERSION_INVALID')
    if 'System answer precision [' in system_prompt:
        raise ValueError('SYSTEM_PROMPT_CANDIDATE_ALREADY_APPLIED')
    return system_prompt + '\n\n' + CANDIDATE_SUFFIXES[version]


def candidate_manifest(system_prompt, *, version=CANDIDATE_VERSION):
    candidate = apply_candidate(system_prompt, version=version)
    manifest = {'version': version,
                'base_sha256': hashlib.sha256(system_prompt.encode()).hexdigest(),
                'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                'added_utf8_bytes': len(candidate.encode()) - len(system_prompt.encode()),
                'real_quality_status': 'PAIRED_EVAL_PENDING_AUTH',
                'default_changed': False}
    # Preserve the original v1 manifest contract for existing replay callers.
    if version != CANDIDATE_VERSION:
        manifest['suffix_sha256'] = (hashlib.sha256(b'').hexdigest() if version == 'off'
                                     else CANDIDATE_SUFFIX_SHA256[version])
        manifest['real_quality_status'] = ('OFF' if version == 'off'
                                           else 'DEVELOPMENT_SET_EVALUATED')
    return manifest
