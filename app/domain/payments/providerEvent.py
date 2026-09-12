from enum import Enum


class ProviderEvent(Enum):
    """The four things a payment provider can tell us about money in flight.

    A closed set, for the reason decision 6 gives: a fixed concept gets an enum,
    so a name the code does not handle is a refusal at the edge rather than a
    value that flows inward and is dropped in silence somewhere.

    **Values are our words, not the wire's.** ``"charge_succeeded"`` rather than
    ``"charge.success"`` is a deliberate split of responsibility: the route that
    receives a webhook owns the translation from Paystack's vocabulary into
    these, and the domain is never handed a string a third party chose. That is
    the same boundary ``dependencies.bearer_token`` draws when it refuses an
    unrecognised scheme at the edge instead of passing the raw header down - and
    it is what makes swapping providers a change to one table in one file rather
    than a search for a dotted string across the codebase.

    **Four members, two directions.** A charge is money coming in and a transfer
    is money going out, and the asymmetry between them is the whole table:

      - ``CHARGE_SUCCEEDED`` is the only event that *credits* a wallet. Nothing
        was taken out at intent time, so this is where the money first exists.
      - ``TRANSFER_SUCCEEDED`` credits nothing, because the wallet was already
        debited when the transfer was requested - the row simply stops being
        PENDING.
      - ``TRANSFER_FAILED`` gives a hold back. The money never left, so it
        returns to the available balance - which is ``release_hold``, and is the
        event that closes the gap a pending payout used to fall into.
      - ``TRANSFER_REVERSED`` gives money back that *had* left and was returned
        by the bank afterwards, which is a different fact from a transfer that
        never landed: the row was SUCCESSFUL and becomes REVERSED.

    The last two are easy to conflate and must not be. A failed transfer is a
    movement that never happened; a reversed one is a movement that happened and
    was undone. They lead to the same credit for different reasons, and only the
    second may touch a row that was already SUCCESSFUL.
    """

    CHARGE_SUCCEEDED = "charge_succeeded"
    """Money arrived from outside. Credits the wallet."""

    TRANSFER_SUCCEEDED = "transfer_succeeded"
    """Money sent to a bank account landed. Credits nothing - it was debited."""

    TRANSFER_FAILED = "transfer_failed"
    """Money sent to a bank account never left. Gives the hold back."""

    TRANSFER_REVERSED = "transfer_reversed"
    """Money that landed was returned afterwards. Gives it back."""
