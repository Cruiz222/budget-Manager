"""The words. One function per kind, and none of them sends anything.

Composing is separated from delivering for the same reason it is in
``NotifyUpcomingRuns._compose``: the text is frozen into the row at the moment
the event happens, so the queue is the authoritative record of what was owed and
the channel has exactly one job - to put those bytes on the wire. Words written
at send time would let two channels word one event differently, and would
silently rewrite messages already sitting in the queue when a sentence changed.

**Every function here is pure.** No clock is read, no store is opened, nothing is
sent. The moment comes from the event that caused the message - ``run.recorded_at``
for a plan run, ``transaction.completed_at`` for a ledger row - so a receipt is
stamped by the thing it describes rather than by a second reading of the clock
that could disagree with it. That is also what makes these functions trivial to
test: no freezing time, no fixtures beyond the aggregates themselves.

**Each returns ``Notification | None``**, and ``None`` means "this installation
has nowhere to send" - no recipient is configured, which is the ordinary state of
a fresh install. The check lives in the private builder so that every kind gets
it for free and none can forget it. The alternative - raising - would make a user
who has not configured email unable to deposit money.

A message is built from the *event's own facts*: the plan's instructions say what
was paid and to whom, the transaction says what moved. Nothing is re-derived from
current state, because current state is exactly what a receipt must not describe -
a plan edited after a run must not change the receipt for the run that already
happened.
"""

from app.domain.money.transaction import Transaction
from app.domain.money.wallet import Wallet
from app.domain.notifications.eventKey import for_plan_event, for_wallet_event
from app.domain.notifications.notification import Notification
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.planning.planRun import PlanRun
from app.domain.planning.savingsPlan import SavingsPlan

#: How every receipt ends. One constant rather than a sentence repeated in five
#: builders, because the thing it says is a property of *receipts*, not of any
#: particular kind: an event has already happened, so there is nothing to act on.
#: The warning this phase sits beside makes the opposite point at the same length
#: (see ``NotifyUpcomingRuns``: "the payout will go ahead whether or not you act
#: on it"), and the two must not blur into each other.
_RECEIPT = (
    "The money has already moved. This is a receipt, not a request - "
    "there is nothing here to confirm.\n"
)


def payout_succeeded(
    plan: SavingsPlan,
    run: PlanRun,
    recipient: str | None,
) -> Notification | None:
    """The receipt for a run that paid out.

    One message for the whole run, not one per instruction. A plan paying three
    accounts is one thing that happened to the person reading the mail; three
    emails would arrive together and have to be reassembled by hand. The
    instructions are listed in the body instead, which is the same information
    in the order it was actually executed.

    The lines are rendered by ``Instruction.__str__`` rather than formatted here.
    That is not tidiness - it is the only way the receipt and the ``plan show``
    output can be guaranteed to describe an instruction the same way, and a
    second renderer would be free to drift from the first.

    Note what this does *not* say: that the run "succeeded" in the abstract. It
    says what moved and where. A run can hold a release, which sends money
    nowhere outside the wallet, and the instruction line says so.
    """
    return _composed(
        event_key=for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, plan.plan_id, run.due_at),
        kind=NotificationKind.PAYOUT_SUCCEEDED,
        subject_id=plan.plan_id,
        recipient=recipient,
        created_at=run.recorded_at,
        subject=f"Plan {plan.name!r}: {plan.total_to_move} moved",
        body=(
            f"The plan {plan.name!r} ran at "
            f"{run.due_at.isoformat(timespec='minutes')}:\n\n"
            f"{_lines(plan)}\n"
            f"Total moved: {plan.total_to_move}.\n\n"
            f"{_RECEIPT}"
        ),
    )


def payout_blocked(
    plan: SavingsPlan,
    run: PlanRun,
    recipient: str | None,
) -> Notification | None:
    """The receipt for a run that could not proceed.

    This is the message a user most needs and least expects. Nothing moved, so
    nothing else in the system will tell them: the wallet is unchanged, there is
    no ledger row, and the only trace is a row in ``plan_runs`` that nobody reads
    unless the plan is already known to be stuck. Without this, a plan that
    cannot be funded stalls in silence.

    It says the plan is *paused*, and that is the load-bearing sentence. A
    blocked run does not advance the plan's counter, so the missed occurrence is
    still owed - which means this is a "stopped, and still owes the money", not
    a "skipped". Resuming picks up the same occurrence. A reader who believes the
    opposite will top up the wallet and wonder why nothing happens.

    ``run.reason`` is read without a fallback, because ``PlanRun`` refuses to
    exist as BLOCKED without one. A default here would paper over a broken
    aggregate with a receipt that explains nothing.
    """
    phrase = run.reason.value.replace("_", " ")
    return _composed(
        event_key=for_plan_event(NotificationKind.PAYOUT_BLOCKED, plan.plan_id, run.due_at),
        kind=NotificationKind.PAYOUT_BLOCKED,
        subject_id=plan.plan_id,
        recipient=recipient,
        created_at=run.recorded_at,
        subject=f"Plan {plan.name!r} could not run",
        body=(
            f"The plan {plan.name!r} was due at "
            f"{run.due_at.isoformat(timespec='minutes')} but did not run: "
            f"{phrase}.\n\n"
            "Nothing was paid. The plan is now paused, and will not try again "
            "until you resume it - resuming picks up this same occurrence rather "
            "than skipping it.\n\n"
            f"It was due to move:\n\n"
            f"{_lines(plan)}\n"
            f"Total: {plan.total_to_move}.\n\n"
            "This is a receipt, not a request: no money moved, and there is "
            "nothing to confirm.\n"
        ),
    )


def wallet_movement(
    kind: NotificationKind,
    wallet: Wallet,
    transaction: Transaction,
    recipient: str | None,
) -> Notification | None:
    """The receipt for one wallet operation, from the row that recorded it.

    Built from the ``Transaction`` the operation *returned*, not from the amount
    the caller passed in. The two differ on the idempotent path: repeating an
    operation with a reference already seen returns the **existing** ledger row
    and moves no money, so the returned row is the only honest source for what
    happened. (The enqueue is a no-op in that case anyway - the derived key is
    already there - but a receipt that only happens to be right because it was
    dropped is a receipt built on a coincidence.)

    The balances come from the ``Wallet`` the caller holds, which the operation
    has already mutated. Reporting the *resulting* balance is most of the value
    of a receipt: "5,000 arrived" is a fact, "you now hold 12,000" is the answer
    to the question the reader actually opened the message with. It is also
    correct on the idempotent path, where the wallet was loaded fresh and left
    untouched - its balances are current either way.

    ``transaction.destination`` is interpolated without a check although a
    payout's is optional on the class. It is not optional here: ``Transaction``
    refuses to exist as a PAYOUT with no destination, which is the same trust
    ``payout_blocked`` places in ``run.reason``.
    """
    # A headline and a sentence, written together because they say the same thing
    # at two different lengths - splitting the branch in two would let a subject
    # and its body drift into disagreeing about which way the money went.
    if kind is NotificationKind.WALLET_DEPOSIT:
        headline = f"Deposited {transaction.amount}"
        sentence = f"{transaction.amount} arrived in the wallet from outside."
    elif kind is NotificationKind.WALLET_WITHDRAWAL:
        headline = f"Withdrew {transaction.amount}"
        sentence = f"{transaction.amount} left the wallet."
    elif kind is NotificationKind.WALLET_PAYOUT:
        headline = f"Paid {transaction.amount} to {transaction.destination}"
        sentence = f"{transaction.amount} was paid to {transaction.destination}."
    else:
        # ``WalletService.ANNOUNCED`` and this function are two halves of one
        # list, and this kind is in one of them. Guessing a shape here would send
        # a receipt that confidently describes the wrong event, which is worse
        # than a loud failure the first time it runs.
        raise ValueError(
            f"{kind.value} is not a wallet movement; "
            "WalletService.ANNOUNCED and compose.wallet_movement disagree"
        )

    return _composed(
        event_key=for_wallet_event(kind, wallet.wallet_id, transaction.internal_reference),
        kind=kind,
        subject_id=wallet.wallet_id,
        recipient=recipient,
        created_at=transaction.completed_at,
        # The reference in the subject, not just the body, because it is the key
        # a user reconciles against their bank statement - and because it is what
        # makes two otherwise identical deposits distinguishable in a list view.
        subject=f"{headline} ({transaction.internal_reference})",
        body=(
            f"{sentence}\n\n"
            f"Wallet {wallet.wallet_id}\n"
            f"Reference: {transaction.internal_reference}\n\n"
            f"Balances now:\n"
            f"  available  {wallet.available_balance}\n"
            f"  locked     {wallet.locked_balance}\n\n"
            f"{_RECEIPT}"
        ),
    )


def _composed(
    *,
    event_key: str,
    kind: NotificationKind,
    subject_id,
    recipient: str | None,
    created_at,
    subject: str,
    body: str,
) -> Notification | None:
    """Build the notification, or ``None`` when there is nowhere to send it.

    The single place the "no recipient" rule lives, so that adding a kind cannot
    forget it. Note the words above are built either way and then discarded -
    formatting strings is free and has no side effects, and pushing the check up
    into each caller would mean five copies of it, one of which would eventually
    be written as ``if recipient:`` and quietly differ.
    """
    if recipient is None:
        return None

    return Notification(
        event_key=event_key,
        kind=kind,
        subject_id=subject_id,
        recipient=recipient,
        subject=subject,
        body=body,
        created_at=created_at,
    )


def _lines(plan: SavingsPlan) -> str:
    """The plan's instructions, one per line and indented.

    Rendered through the instruction's own ``__str__`` - see
    ``payout_succeeded`` for why that matters more than it looks.
    """
    return "".join(f"  {instruction}\n" for instruction in plan.instructions)
