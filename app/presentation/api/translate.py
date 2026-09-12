"""Where the wire vocabulary is decided, in both directions.

``schemas`` says what the shapes *are*; this module says what they *mean*. The
split matters because a field can be the right type and still be the wrong value:
``"banana"`` is a perfectly good string for a cadence and not a cadence at all.
Every conversion between a string on the wire and an object in the domain happens
here, so there is one file to read to answer "what does the API accept, and what
does it send back?" - and one file to change when either answer does.

**Request-side conversions raise domain errors, not ValueError.** This is the
thing to get right, and it is easy to get wrong: ``PlanSource("banana")`` raises
a plain ``ValueError``, which is not a ``MoneyError``, so it would fall through
every handler in ``errors`` and come back as a 500 - telling a client that the
server is broken when they sent a typo. Every enum below is therefore read
through ``_member``, which turns the same failure into the error the domain
already has for that concept. The CLI never has this problem because argparse
checks ``choices`` before any of this code runs; HTTP has no argparse, so the
check has to be written once, here.

**Response-side conversions are the only place a domain object becomes JSON.**
Money, in particular, is formatted in exactly one function - which is what keeps
``"8000.00"`` from being ``"8000"`` in one endpoint and ``"8000.0"`` in another.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from app.application.identity.log_in import LoggedIn
from app.domain.identity.user import User
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    InvalidAmountError,
    InvalidDestinationKindError,
    InvalidFundKindError,
    UnsupportedCurrencyError,
)
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.wallet import Wallet
from app.domain.planning.cadence import Cadence
from app.domain.planning.duration import Duration
from app.domain.planning.durationUnit import DurationUnit
from app.domain.planning.exception import (
    InvalidDurationUnitError,
    InvalidInstructionActionError,
    InvalidPlanSourceError,
    InvalidScheduleCadenceError,
)
from app.domain.planning.instruction import Instruction
from app.domain.planning.planRun import PlanRun
from app.domain.planning.planSource import PlanSource
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.planning.schedule import Schedule
from app.presentation.api import schemas


def _member(enum_type, value, error, what: str):
    """Read an enum from its wire spelling, or refuse it as a domain error.

    The ``allowed`` list is built from the enum rather than written out, so a
    member added later appears in the message without anybody remembering to add
    it. ``app.presentation.cli._end_date`` does the same thing for the term unit,
    for the same reason.
    """
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(one.value for one in enum_type)
        raise error(f"{what} must be one of {allowed}, got {value!r}")


# --- domain -> wire ---------------------------------------------------------


def money_out(money: Money) -> schemas.MoneyOut:
    """An amount as a string, always with two decimal places.

    The format spec is the one ``Money.__str__`` uses, deliberately: the CLI
    prints ``8000.00 NGN`` and this sends ``"8000.00"``, so a person reading one
    and a client reading the other are looking at the same number written the same
    way.

    The ``:.2f`` is doing real work rather than tidying. ``Decimal`` keeps the
    exponent it was built with, so ``Money(8000)`` formats as ``"8000"`` and
    ``Money(Decimal("8000.00"))`` as ``"8000.00"`` - both correct, both the same
    money, and a wire format that varied with how the value happened to be
    constructed would be a wire format no client could rely on.
    """
    return schemas.MoneyOut(
        amount=f"{money.amount:.2f}", currency=money.currency.value
    )


def destination_out(destination: Destination | None) -> schemas.DestinationOut | None:
    if destination is None:
        return None
    return schemas.DestinationOut(
        kind=destination.kind.value,
        identifier=destination.identifier,
        name=destination.name,
        details=dict(destination.details),
    )


def instruction_out(instruction: Instruction) -> schemas.InstructionOut:
    return schemas.InstructionOut(
        action=instruction.action.value,
        amount=money_out(instruction.amount),
        label=instruction.label,
        destination=destination_out(instruction.destination),
    )


def fund_out(fund: Fund) -> schemas.FundOut:
    """A pot, with the one derived fact a client would otherwise re-decide.

    ``is_open`` is ``maturity_date is None`` - "this pot never had a date",
    which is a different question from "has its date passed". The date travels
    too, so a client could work the boolean out for itself; it is sent anyway
    because ``None`` here means *no maturity ever* and not *unknown*, and a
    client that read it as the latter would get the pot's whole behaviour
    backwards. Sending the answer means there is one definition of it and it is
    the aggregate's - ``Fund.is_open`` is the only one, and this is its only
    caller.

    ``is_matured`` is deliberately *not* sent, and the omission is the mirror of
    the same reasoning: it is a question about a moment, and a moment sent from
    here would be this server's clock frozen into a value the client holds. When
    something needs that answer, the request that needs it can carry the moment.
    """
    return schemas.FundOut(
        fund_id=fund.fund_id,
        name=fund.name,
        kind=fund.kind.value,
        balance=money_out(fund.balance),
        maturity_date=fund.maturity_date,
        first_funded_at=fund.first_funded_at,
        created_at=fund.created_at,
        sealed_at=fund.sealed_at,
        is_open=fund.is_open,
    )


def wallet_out(wallet: Wallet) -> schemas.WalletOut:
    return schemas.WalletOut(
        wallet_id=wallet.wallet_id,
        user_id=wallet.user_id,
        currency=wallet.currency.value,
        status=wallet.status.value,
        available_balance=money_out(wallet.available_balance),
        locked_balance=money_out(wallet.locked_balance),
        funds=[fund_out(fund) for fund in wallet.funds],
    )


def transaction_out(transaction: Transaction) -> schemas.TransactionOut:
    return schemas.TransactionOut(
        transaction_id=transaction.transaction_id,
        type=transaction.type.value,
        amount=money_out(transaction.amount),
        status=transaction.status.value,
        internal_reference=transaction.internal_reference,
        provider_reference=transaction.provider_reference,
        narration=transaction.narration,
        destination=destination_out(transaction.destination),
        fund_id=transaction.fund_id,
        created_at=transaction.created_at,
        completed_at=transaction.completed_at,
        reversed_at=transaction.reversed_at,
    )


def schedule_out(schedule: Schedule) -> schemas.ScheduleOut:
    return schemas.ScheduleOut(
        cadence=schedule.cadence.value, anchor=schedule.anchor
    )


def plan_out(plan: SavingsPlan) -> schemas.PlanOut:
    return schemas.PlanOut(
        plan_id=plan.plan_id,
        wallet_id=plan.wallet_id,
        name=plan.name,
        source=plan.source.value,
        status=plan.status.value,
        schedule=schedule_out(plan.schedule),
        instructions=[instruction_out(one) for one in plan.instructions],
        total_to_move=money_out(plan.total_to_move),
        completed_runs=plan.completed_runs,
        next_due_at=plan.next_due_at,
        fund_id=plan.fund_id,
        ends_on=plan.ends_on,
        created_at=plan.created_at,
        is_irreversible=plan.is_irreversible,
    )


def plan_run_out(run: PlanRun) -> schemas.PlanRunOut:
    return schemas.PlanRunOut(
        plan_id=run.plan_id,
        due_at=run.due_at,
        status=run.status.value,
        recorded_at=run.recorded_at,
        reason=run.reason.value if run.reason is not None else None,
    )


def user_out(user: User) -> schemas.UserOut:
    """Who the caller is.

    No ``google_subject``, and the omission is a decision rather than an
    oversight: it is an identifier issued by a third party, it is the key a
    future sign-in would match on, and no client of this API has a use for it.
    What a client needs from this endpoint is "which account am I", and an id and
    an address answer that.
    """
    return schemas.UserOut(
        user_id=user.user_id, email=user.email, created_at=user.created_at
    )


def session_out(logged_in: LoggedIn) -> schemas.SessionOut:
    """A successful login, as the client sees it.

    **This is the only place a token crosses into a response**, and it takes a
    ``LoggedIn`` rather than its three parts so that the crossing is a single
    line a reader can find. The session's ``token_hash`` is not what goes out -
    it cannot be, since ``Session`` holds no token - so there is no version of
    this function that could accidentally send the stored value instead of the
    presentable one. The type is what rules it out, not the care taken here.

    ``user_out`` is reused rather than the fields being repeated, so that a
    login and a ``/users/me`` describe the same account in the same words. If
    ``UserOut`` ever grows a field, both answers grow it together.
    """
    return schemas.SessionOut(
        token=logged_in.token,
        expires_at=logged_in.session.expires_at,
        user=user_out(logged_in.user),
    )


# --- wire -> domain ---------------------------------------------------------


def money_in(text: str, currency: Currency) -> Money:
    """Read an amount in the wallet's currency.

    The wallet supplies the currency, exactly as it does on the command line: a
    bare number has no currency of its own, and inventing one here would be a
    second opinion about which money a plan spends - the very rule
    ``PlanService.create_plan`` exists to enforce against the wallet.

    A failure is raised as ``InvalidAmountError``, a ``MoneyError``, so it reaches
    the client as a 400 naming the value. ``Decimal``'s own ``InvalidOperation``
    is a ``ValueError`` and would be a 500 - the same trap ``_member`` exists to
    avoid, in the one other place a string becomes a domain object.
    """
    try:
        return Money(Decimal(text), currency)
    except InvalidOperation:
        raise InvalidAmountError(f"invalid amount: {text!r}")


def currency_in(text: str) -> Currency:
    return _member(
        Currency, text, UnsupportedCurrencyError, "currency"
    )


def destination_in(payload: schemas.DestinationIn) -> Destination:
    return Destination(
        kind=_member(
            DestinationKind,
            payload.kind,
            InvalidDestinationKindError,
            "destination kind",
        ),
        identifier=payload.identifier,
        name=payload.name,
        details=dict(payload.details),
    )


def instructions_in(
    payloads: list[schemas.InstructionIn], currency: Currency
) -> tuple[Instruction, ...]:
    """Read a plan's lines, in the wallet's currency.

    A tuple, because that is what ``SavingsPlan`` demands of its lines - it
    refuses a list so nobody can hold a mutable reference and append past every
    check. This is the same shape ``app.presentation.cli._lines`` builds, from the
    same two request fields, so a plan created over HTTP and one created at a
    terminal are indistinguishable once they are stored.
    """
    instructions = []
    for payload in payloads:
        instructions.append(
            Instruction(
                action=_member(
                    PlannedAction,
                    payload.action,
                    InvalidInstructionActionError,
                    "instruction action",
                ),
                amount=money_in(payload.amount, currency),
                label=payload.label,
                destination=(
                    destination_in(payload.destination)
                    if payload.destination is not None
                    else None
                ),
            )
        )
    return tuple(instructions)


def schedule_in(payload: schemas.ScheduleIn) -> Schedule:
    return Schedule(
        cadence=_member(
            Cadence, payload.cadence, InvalidScheduleCadenceError, "cadence"
        ),
        anchor=payload.anchor,
    )


def fund_kind_in(text: str) -> FundKind:
    return _member(FundKind, text, InvalidFundKindError, "fund kind")


def end_date_in(payload: schemas.CreatePlanIn) -> date | None:
    """The plan's end date, from ``ends_on`` or from ``term``.

    Resolved against the anchor rather than against today, and by the domain's
    own ``Duration.end_from`` - the same call the CLI makes in ``_end_date``.
    Neither presentation owns this rule: a plan made "for 12 months" ends on the
    same day whichever door it came through, and the way to guarantee that is for
    there to be one implementation of the arithmetic, which there is.
    """
    if payload.ends_on is not None:
        return payload.ends_on
    if payload.term is None:
        return None
    unit = _member(
        DurationUnit, payload.term.unit, InvalidDurationUnitError, "term unit"
    )
    # The anchor is a datetime and may be timezone-aware if the client sent an
    # offset; ``end_from`` reduces the result to a day either way, so the extra
    # precision is discarded deliberately rather than lost.
    return Duration(payload.term.amount, unit).end_from(payload.schedule.anchor)


def source_in(text: str) -> PlanSource:
    return _member(PlanSource, text, InvalidPlanSourceError, "plan source")
