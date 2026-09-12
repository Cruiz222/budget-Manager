from dataclasses import dataclass
from enum import Enum


class SettlementOutcome(Enum):
    """Every answer this system can give a payment provider, refusals included.

    **The refusals are members of the same enum as the successes, and that is the
    design rather than a shortcut.** A webhook's honest answer to "this reference
    is unknown", "this event arrived twice" and "this amount does not match what
    the row asked for" is the same as its answer to "the money arrived": an
    acknowledgement, a log line, and no exception. Making the refusals a separate
    error type would force the route to catch it and translate back into a 200 -
    which is a catch site that exists only to undo a decision, and the kind of
    place a future author adds a 500 without noticing what it costs. A payment
    provider that receives a 500 retries, and a retry of something that will
    never succeed retries forever.

    So there is one enum, and it is the whole vocabulary of what can happen:

      - ``DEPOSIT_CREDITED``, ``TRANSFER_SETTLED``, ``HOLD_RELEASED`` and
        ``PAYMENT_REVERSED`` each describe a movement that **did** happen.
      - ``UNKNOWN_REFERENCE``, ``AMOUNT_DISAGREES``, ``ALREADY_SETTLED``,
        ``WRONG_KIND`` and ``WALLET_CLOSED`` each describe why nothing happened.
        They are deliberately separate rather than one ``IGNORED``: they call for
        different responses from whoever reads the log. An unknown reference is a
        deployment talking to the wrong database, or a forged call that got past
        the signature; a disagreement is a bug or tampering; a repeat is the
        system working; a wrong kind is a provider that changed what an event
        means; and a closed wallet is money that has arrived and has nowhere to
        go, which is the one of the five that needs a person.

    **``EVENT_IGNORED`` is the one member no use case produces**, and it belongs
    here anyway rather than as a string literal in the route. It is what is left
    when an event is understood well enough to know that this deployment does not
    act on it - so it is not a settlement that failed, it is a decision that
    there was nothing to settle. Keeping it out of this enum would mean the
    webhook's response body had two vocabularies with a boundary nobody could
    see, and the one place a reader looked to find out what happened to their
    money would be the place that answered "something else, in a different
    shape".
    """

    DEPOSIT_CREDITED = "deposit_credited"
    """A charge arrived. The wallet was credited and the row is SUCCESSFUL."""

    TRANSFER_SETTLED = "transfer_settled"
    """A transfer landed. The row is SUCCESSFUL; no balance changed."""

    HOLD_RELEASED = "hold_released"
    """A transfer failed. The hold went back and the row is FAILED."""

    PAYMENT_REVERSED = "payment_reversed"
    """A settled payout was returned. The money went back; the row is REVERSED."""

    UNKNOWN_REFERENCE = "unknown_reference"
    """No ledger row is filed under this reference. Nothing moved."""

    AMOUNT_DISAGREES = "amount_disagrees"
    """The event's amount is not the row's amount. Nothing moved."""

    ALREADY_SETTLED = "already_settled"
    """The row is not PENDING, so this event has been applied before."""

    WRONG_KIND = "wrong_kind"
    """This event cannot describe this row - a charge for a payout, say."""

    WALLET_CLOSED = "wallet_closed"
    """The money arrived for a wallet that cannot be credited. Nothing moved."""

    EVENT_IGNORED = "event_ignored"
    """This installation does not act on this kind of event. Nothing moved."""


@dataclass(frozen=True)
class SettledPayment:
    """What ``SettlePayment`` did, in a form a route can log and a test can assert.

    ``outcome`` is the assertion surface and ``detail`` is for the log, and the
    split is deliberate: a test that pinned the prose would fail on every
    rewording, and a test that pinned only the enum would not notice the detail
    line going missing. Each has one job.

    There is no transaction id here, and its absence is worth a sentence. A route
    answering a webhook has nowhere to put one - the provider does not read our
    response body, and a reference is all it would understand if it did - so
    carrying one would be carrying a field nothing consumes. Callers that want
    the row read the ledger, which is the same rule ``WalletService._announce``
    follows when it declines to build a receipt from anything but the row.
    """

    outcome: SettlementOutcome
    reference: str
    detail: str

    @property
    def moved_money(self) -> bool:
        """Whether this settle changed a balance.

        True for the four members that describe a movement and false for every
        refusal - which is what makes it useful in a test ("assert nothing
        moved") without re-listing the members, and what keeps a further refusal
        from silently reading as a movement the day it is added.
        """
        return self.outcome in _MOVEMENTS


#: The four outcomes that changed something. Named rather than derived, because
#: the alternative - a naming convention, or checking the value string for a
#: prefix - would be a rule the next member could break without noticing.
_MOVEMENTS = frozenset(
    {
        SettlementOutcome.DEPOSIT_CREDITED,
        SettlementOutcome.TRANSFER_SETTLED,
        SettlementOutcome.HOLD_RELEASED,
        SettlementOutcome.PAYMENT_REVERSED,
    }
)
