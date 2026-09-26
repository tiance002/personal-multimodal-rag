class DomainError(Exception):
    """Base class for stable application-domain failures."""

    code = "DOMAIN_ERROR"


class ScopeViolation(DomainError):
    code = "SCOPE_VIOLATION"


class EvidenceIntegrityError(DomainError):
    code = "EVIDENCE_INVALID"
