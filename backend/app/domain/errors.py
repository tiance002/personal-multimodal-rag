class DomainError(Exception):
    """Base class for stable application-domain failures."""

    code = "DOMAIN_ERROR"


class ScopeViolation(DomainError):
    code = "SCOPE_VIOLATION"


class EvidenceIntegrityError(DomainError):
    code = "EVIDENCE_INVALID"


class FinalAnswerCommitError(DomainError):
    """A success transaction was rolled back before writing answer artifacts."""

    code = "FINAL_ANSWER_COMMIT_INVALID"
