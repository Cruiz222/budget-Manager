from enum import Enum


class ProviderAnswerStatus(Enum):
    """The three things a provider can answer when asked about a reference.

    This is the *asking* direction, and it is a different conversation from the
    one ``ProviderEvent`` describes. An event is pushed at us and says what
    happened; an answer is pulled by us and says what the provider knows. The two
    are not interchangeable and must not be collapsed into one enum, because a
    provider that has never heard of a reference has no event to report - there
    is nothing it could push.

    **Three members, and the third is the one that earns the type.** Two would
    fit in ``ProviderOutcome | None`` and that shape was rejected, because
    ``None`` would have to mean both "not settled yet" and "no such reference",
    and those two facts send an operator to opposite ends of the system. The
    first is a payer who has not paid. The second is a deployment whose rows and
    whose secret key do not belong to each other, and reporting it as the first
    would be a diagnosis that sends somebody looking in exactly the wrong place -
    the same complaint ``verify_signature``'s port docstring makes about a
    re-serialized body presenting as a wrong secret key.

    **``NO_SUCH_REFERENCE`` and ``SettlementOutcome.UNKNOWN_REFERENCE`` are
    opposite ends of the wire**, and the near-identical names are the hazard this
    paragraph exists to defuse. ``UNKNOWN_REFERENCE`` is our answer to a webhook
    naming a row we do not have: the provider knows about a payment and we do
    not. ``NO_SUCH_REFERENCE`` is the provider's answer to a question about a row
    *we* have: we know about a payment and it does not. A shared name would make
    a log line about one of them read as a log line about the other, and an
    operator debugging a phantom webhook would be sent to check a key that is
    fine.

    Values are lowercase names, matching ``ProviderEvent`` and ``ConfirmationKind``.
    They are our words rather than Paystack's: the adapter owns the translation
    from ``data.status``, and the domain is never handed a string a third party
    chose.
    """

    SETTLED = "settled"
    """The provider has this money, and it has arrived.

    The only answer that carries an outcome, because it is the only one that
    describes something that happened. Not a synonym for "successful" on our
    side - ``SettlePayment`` still has its own refusals to make, an amount that
    disagrees above all.
    """

    NOT_SETTLED = "not_settled"
    """The provider knows the reference and no money has arrived under it.

    Deliberately one member rather than several. Paystack distinguishes
    ``abandoned``, ``ongoing``, ``pending``, ``processing`` and ``queued``, and
    every one of those differences is a difference *the provider* cares about -
    a checkout the payer is still looking at and a checkout they closed are the
    same fact from here: nothing has settled, and asking again later is the only
    correct response to either. Collapsing them is what keeps a payer who is
    still on the payment page from having their row failed underneath them.
    """

    NO_SUCH_REFERENCE = "no_such_reference"
    """The provider has no transaction filed under this reference at all.

    Unreachable in a healthy installation, and that is precisely what makes it
    worth a member. ``InitiateDeposit`` writes a ledger row only after the
    provider accepted the call, so every reference we hold a row for is one the
    provider was told about - and one it now denies knowing means the rows in
    this database were written against a different account. It is an operational
    alarm rather than a payment state, which is why nothing is settled, failed or
    changed when it arrives.
    """
