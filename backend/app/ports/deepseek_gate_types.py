"""Concrete gate identities supplied by the trusted composition root.

No registration, egress permission, ledger access or accounting occurs here.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class DeepSeekGateTypes:
    session: type
    validation: type
    rag: type
    static_pair: type
    immutable_batch: type
    product: type
    product_receipt: type

    @property
    def supported(self) -> tuple[type, ...]:
        return (self.session, self.validation, self.rag, self.static_pair,
                self.immutable_batch, self.product)
