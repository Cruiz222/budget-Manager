from dataclasses import dataclass

from app.domain.payments.exception import InvalidProviderAnswerError
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerOutcome import ProviderOutcome


@dataclass(frozen=True)
class ProviderAnswer:
    """What a provider said when we asked it about a reference.

    ``ProviderOutcome`` is what a provider *tells* us; this is what it *answers*.
    Both describe money in flight, and only one of them can arrive uninvited.

    **The two fields are coupled, and ``__post_init__`` enforces it.** An
    ``outcome`` is present exactly when the status is ``SETTLED``, because an
    outcome is a description of something that happened and there is nothing to
    describe otherwise. The alternative - letting a ``NOT_SETTLED`` answer carry
    a stray outcome, or a ``SETTLED`` one carry nothing - would put the
    invariant at each call site instead of in the type, and the call site that
    got it wrong would be the one reading ``answer.outcome.event`` on an answer
    that has none. This is the rule ``Transaction`` and ``Confirmation`` already
    keep: an impossible record should not be constructible.

    Note what is *not* here: an amount for the non-settled answers. A provider
    that has collected nothing has no amount to report, and defaulting one in -
    zero, or the amount we asked for - would be inventing a fact and then
    handing it to the mismatch rule, which would compare it and refuse. The
    absence is refused here rather than defaulted away, which is
    ``ProviderOutcome``'s own reasoning about its amount one field over.
    """

    status: ProviderAnswerStatus
    outcome: ProviderOutcome | None = None

    def __post_init__(self):
        if not isinstance(self.status, ProviderAnswerStatus):
            raise InvalidProviderAnswerError(
                f"status must be a ProviderAnswerStatus, "
                f"not {type(self.status).__name__}"
            )

        if self.status is ProviderAnswerStatus.SETTLED:
            if not isinstance(self.outcome, ProviderOutcome):
                raise InvalidProviderAnswerError(
                    f"a SETTLED answer must carry a ProviderOutcome, "
                    f"not {type(self.outcome).__name__}"
                )
            return

        if self.outcome is not None:
            raise InvalidProviderAnswerError(
                f"a {self.status.value} answer describes something that did not "
                f"happen and must carry no outcome"
            )
