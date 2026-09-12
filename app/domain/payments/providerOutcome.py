from dataclasses import dataclass

from app.domain.money.money import Money
from app.domain.payments.exception import InvalidProviderOutcomeError
from app.domain.payments.providerEvent import ProviderEvent


@dataclass(frozen=True)
class ProviderOutcome:
    """One thing a provider has told us happened, as three facts.

    A reference, an event and an amount - and the three are exactly what a
    webhook carries and exactly what ``SettlePayment`` needs. Grouping them is
    not tidiness: it is the boundary between "bytes that arrived" and "a claim
    about money", and it is the type that says the parsing is *finished*. A
    route that has a ``ProviderOutcome`` in hand has already decided what the
    payload meant, so ``SettlePayment`` never sees a string a third party chose
    and never has to ask what an event name was supposed to be.

    **The amount is required, and the reason is the mismatch rule.** Settlement
    compares what arrived against what the row asked for and refuses to move
    anything if they disagree. An outcome with no amount would be one that could
    never disagree, which is the same thing as a rule that silently does not
    apply - so the absence is refused here rather than defaulted away.

    Note that this is what the *provider* says, not what we do about it: the
    answer to "and then what?" is ``SettledPayment``, which is a different type
    in a different layer. Keeping them apart is what lets a test assert that a
    well-formed event was understood and still settled nothing.
    """

    event: ProviderEvent
    reference: str
    amount: Money

    def __post_init__(self):
        if not isinstance(self.event, ProviderEvent):
            raise InvalidProviderOutcomeError(
                f"event must be a ProviderEvent, not {type(self.event).__name__}"
            )

        if not isinstance(self.reference, str) or not self.reference.strip():
            raise InvalidProviderOutcomeError("reference must be a non-empty string")

        if not isinstance(self.amount, Money):
            raise InvalidProviderOutcomeError(
                f"amount must be a Money, not {type(self.amount).__name__}"
            )

        if self.amount.amount <= 0:
            raise InvalidProviderOutcomeError("amount must be greater than zero")
