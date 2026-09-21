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

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.application.identity.confirm_email_change import ConfirmedEmailChange
from app.application.identity.confirm_password_reset import ConfirmedPasswordReset
from app.application.identity.log_in import LoggedIn
from app.application.identity.request_email_change import EmailChangeOutcome
from app.application.identity.request_password_reset import PasswordResetOutcome
from app.application.payments.initiate_deposit import InitiatedDeposit
from app.application.profile_service import ProfileStanding
from app.application.wallet_service import ConfirmedOperation
from app.domain.identity.phoneVerification import PhoneVerification
from app.domain.identity.tier import Tier, limits_for
from app.domain.identity.user import User
from app.domain.money.confirmation import Confirmation
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


def confirmation_out(
    confirmation: Confirmation, as_of: datetime | None = None
) -> schemas.ConfirmationOut:
    """A recorded request, with the status the caller would be answered with now.

    ``as_of`` defaults to *no moment given*, which reports the stored status
    unchanged - and that is the right default rather than a lazy one, because a
    translation that read the clock would make every response depend on when it
    was rendered, and two calls rendering the same row would disagree for a
    reason no reader could see. The routes pass ``datetime.now()`` explicitly,
    at the same moment they hand the same instant to the service, so the status
    in the body and the status the next call would act on are the same answer.

    ``status_as_of`` is called even when nothing has expired: the derived status
    is the *only* thing this API reports, so ``expired`` comes out of one method
    rather than out of a comparison written here as well. A second comparison
    would be a second definition of the window's last instant, free to disagree
    with ``Confirmation.is_expired`` about the boundary - in the direction of
    telling a client its request is live when confirming it would refuse.
    """
    return schemas.ConfirmationOut(
        confirmation_id=confirmation.confirmation_id,
        kind=confirmation.kind.value,
        status=(
            confirmation.status
            if as_of is None
            else confirmation.status_as_of(as_of)
        ).value,
        wallet_id=confirmation.wallet_id,
        amount=(
            money_out(confirmation.amount)
            if confirmation.amount is not None
            else None
        ),
        destination=destination_out(confirmation.destination),
        fund_name=confirmation.fund_name,
        internal_reference=confirmation.internal_reference,
        created_at=confirmation.created_at,
        expires_at=confirmation.expires_at,
    )


def confirmed_operation_out(
    operation: ConfirmedOperation, as_of: datetime | None = None
) -> schemas.ConfirmedOperationOut:
    """What answering a confirmation produced.

    ``as_of`` is threaded to ``confirmation_out`` and matters for exactly one
    case: a ``CLOSE``, whose request the service reads back after the movement.
    A confirmed request never derives to ``EXPIRED`` - ``status_as_of`` leaves a
    spent request alone however long ago it was spent - so for the three money
    operations the moment changes nothing. It is passed anyway so that the
    answer does not depend on which kind happened to be asked about.

    ``transaction`` is ``None`` for a close, and that is forwarded rather than
    substituted: see ``ConfirmedOperationOut``.
    """
    return schemas.ConfirmedOperationOut(
        confirmation=confirmation_out(operation.confirmation, as_of),
        transaction=(
            transaction_out(operation.transaction)
            if operation.transaction is not None
            else None
        ),
        wallet=wallet_out(operation.wallet),
    )


def initiated_deposit_out(deposit: InitiatedDeposit) -> schemas.DepositIntentOut:
    """A deposit that has been asked for, as the client sees it.

    The four fields pass straight through, which makes this look like a function
    that need not exist - and it exists for the reason every other function in
    this section does: it is the one place that decides what crosses the wire.
    ``money_out`` is doing real work on the amount, and ``status`` is a
    ``TransactionStatus`` becoming its ``.value``. A route that built this
    response itself would be a second place that formats an amount, which is the
    file's whole warning.

    ``authorization_url`` is forwarded unread. It is the provider's page, and the
    only transformation applied to it anywhere in this codebase is the JSON
    encoding a client's HTTP library will do - which is the correct amount of
    handling for a URL that is not ours to interpret.
    """
    return schemas.DepositIntentOut(
        authorization_url=deposit.authorization_url,
        provider_reference=deposit.provider_reference,
        amount=money_out(deposit.amount),
        status=deposit.status.value,
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

    **An absent address is passed through rather than filled in**, because there
    is nothing honest to fill it with: the account genuinely holds none, and
    inventing a placeholder would be the same mistake as giving a phone-only
    account a synthetic address in the column. See ``schemas.UserOut``.

    ``phone`` is passed through on exactly those terms one identifier over, and it
    is here rather than withheld on the argument above run backwards: an address is
    not a secret and neither is a number - both are identifiers the caller handed
    over - and the thing that *is* withheld is ``google_subject``, which the caller
    never chose and never sees. Both fields are ``User.phone``/``User.email`` and
    never ``Profile.phone``, which is free-form KYC data reported by
    ``profile_out``.
    """
    return schemas.UserOut(
        user_id=user.user_id,
        email=user.email,
        phone=user.phone,
        created_at=user.created_at,
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


def email_change_out(outcome: EmailChangeOutcome) -> schemas.EmailChangeOut:
    """A requested address change, as the client sees it.

    **No token, and there is no field here that could carry one.** The token goes
    to the address being moved to and nowhere else - not in this response, not in
    a log, not in the caller's session - which is why ``EmailChangeOutcome`` does
    not expose it either. A client is handed "pending" and a deadline, and the
    only way it ever sees the code is by receiving the mail.

    ``status`` is derived from ``applied`` rather than carried alongside it, so a
    client and this function cannot disagree about which of the two happened. The
    schema holds the word, the outcome holds the fact.
    """
    return schemas.EmailChangeOut(
        status="applied" if outcome.applied else "pending",
        email=outcome.email,
        expires_at=outcome.expires_at,
    )


def email_change_confirmed_out(
    confirmed: ConfirmedEmailChange,
) -> schemas.EmailChangeConfirmedOut:
    """A finished change, with the two facts about the notice passed through unread.

    The notice's outcome is forwarded rather than interpreted, and that is
    deliberate: whether a bounced farewell is worth showing somebody is a question
    for the client's own interface, and this layer's job is to report it honestly
    rather than to decide what it means. See ``EmailChangeConfirmedOut`` for the
    three states the two fields hold.
    """
    return schemas.EmailChangeConfirmedOut(
        user=user_out(confirmed.user),
        previous_email=confirmed.previous_email,
        notice_sent=confirmed.notice_sent,
        notice_error=confirmed.notice_error,
    )


def password_reset_accepted_out(
    outcome: PasswordResetOutcome,
) -> schemas.PasswordResetAcceptedOut:
    """That a reset was asked for, saying nothing about whether an account was found.

    **``outcome`` is deliberately not read**, and that is the whole of this
    function. It carries a ``requested`` flag the CLI reads and this must not:
    branching on it here - to vary a word, a status or a code - is exactly the
    enumeration oracle the endpoint is built to avoid, and it would be a branch
    nobody could see from the response because the response would be right in both
    arms. So the parameter is accepted and dropped, which is more honest than a
    signature with no argument at all: the route has a value in hand, and this is
    the line where it stops.

    The alternative shape - a route that called the service and built the schema
    inline, never touching the outcome - was rejected for a narrower reason than
    security: every other route in this API goes through a translator, and one that
    quietly did not would be the file a reader has to check to find out whether the
    rule above is kept. Here it is kept in the one place the rule would be broken.

    The ``status`` field's default comes from the schema rather than from this
    argument, so there is not even a word here that could depend on the account.
    """
    return schemas.PasswordResetAcceptedOut()


def phone_verification_accepted_out(
    verification: PhoneVerification,
) -> schemas.PhoneVerificationAcceptedOut:
    """That a code was texted, to which number, and when it stops working.

    **This function reads its argument, and the contrast with the translator above
    is the interesting part.** ``password_reset_accepted_out`` takes an outcome and
    drops it, because that endpoint asks the store whether an identifier names an
    account and the answer must not reach the client. This one asks the store
    nothing at all - the number is an address being dialled rather than a key being
    looked up - so there is no hit and no miss for a response to betray, and every
    field here is a fact about the *request* the caller just made: the number they
    typed, folded, and the deadline counted from the moment it was recorded. A
    reader who wonders why one of these two is silent and the other is not needs
    only to look at what each use case reads off the store.

    ``phone`` is ``verification.phone`` rather than anything a caller passed, which
    is what makes this the folded spelling - the same string the row holds, the
    same string the provider was handed, and the same string the ``UNIQUE`` slot
    will be held by if the person answers. Reporting the typed spelling instead
    would be a comfort that agrees with nothing.

    ``expires_at`` is the row's own deadline rather than ``now + lifetime``
    recomputed here, for ``reset_message``'s reason: one reading of the clock, so
    the deadline the person is shown and the deadline the claim enforces cannot be
    counted from two different moments.
    """
    return schemas.PhoneVerificationAcceptedOut(
        phone=verification.phone,
        expires_at=verification.expires_at,
    )


def password_reset_confirmed_out(
    confirmed: ConfirmedPasswordReset,
) -> schemas.PasswordResetConfirmedOut:
    """A replaced password, with the count and the notice's outcome passed through.

    The count is forwarded rather than interpreted, matching ``previous_email`` in
    the translator above: it is a fact about what happened, and whether a client
    should show it, phrase it, or ignore it is the client's business. What this
    layer guarantees is that the number it sends is the number of rows the confirm
    actually deleted, never a fixed one or a guess.

    The notice's outcome is likewise forwarded unread, for the reason
    ``email_change_confirmed_out`` gives: three states in two fields, and deciding
    what a bounced warning means belongs to the interface that shows it.
    """
    return schemas.PasswordResetConfirmedOut(
        user=user_out(confirmed.user),
        sessions_revoked=confirmed.sessions_revoked,
        notice_sent=confirmed.notice_sent,
        notice_error=confirmed.notice_error,
    )


def tier_limits_out(tier: Tier) -> list[schemas.TierLimitOut]:
    """Every ceiling this tier imposes, one row per currency.

    **Every currency rather than the ones an account holds**, which is the
    decision ``ProfileOut`` argues: the limits table is keyed by
    ``(tier, currency)``, it is the same table for everybody at that tier, and
    there is nothing in it that belongs to a person. It is also the only version
    this layer *can* build - a wallet is reachable only by its own id in this
    API, so there is no list of wallets to scope by.

    Iterating ``Currency`` rather than ``LIMITS`` is deliberate and it is the
    completeness test's own shape read from the other end: a currency added to
    the enum with no row in the table would be a ``KeyError`` here, at the
    translation, which is loud. Iterating the table would silently send a
    shorter list and a client rendering a wallet in some other currency would
    find no row and have nothing to say.
    """
    return [
        schemas.TierLimitOut(
            currency=currency.value,
            per_transaction=_optional_money_out(limits_for(tier, currency).per_transaction),
            daily_outflow=_optional_money_out(limits_for(tier, currency).daily_outflow),
            max_balance=_optional_money_out(limits_for(tier, currency).max_balance),
        )
        for currency in Currency
    ]


def _optional_money_out(money: Money | None) -> schemas.MoneyOut | None:
    """A ``Money`` that may be absent, as an amount that may be null.

    ``None`` is a real value on ``TierLimits`` - it is how a tier says a ceiling
    does not apply - so this function exists to carry that across honestly
    rather than to substitute a zero. A zero would be the worst possible
    translation: it reads as "you may not move anything", which is the opposite
    of what an absent ceiling means.

    Private, and the only one of its kind here, because ``money_out``'s callers
    are all dealing with amounts that a movement cannot be recorded without
    (``wallet_out``'s balance, ``transaction_out``'s amount, an instruction).
    This is the one place a domain amount is genuinely optional.
    """
    return None if money is None else money_out(money)


def profile_out(standing: ProfileStanding) -> schemas.ProfileOut:
    """An account's standing, as the client sees it.

    **The three parts are forwarded, and only one of them is derived.** ``tier``
    and ``is_complete`` come straight off the outcome, because the derivation is
    ``tier_for``'s and it already happened inside the service's unit - re-deriving
    here would be a second opinion about the one rule this feature exists to keep
    in one place. ``limits`` is looked up from the tier, which is not a second
    derivation but the *consequence* of it.

    ``profile`` becomes ``None`` when the account has never filled one in, and the
    nesting is what makes that legible rather than a null name inside an object
    that appears to exist. See ``ProfileOut``.

    ``created_at`` and ``updated_at`` are forwarded on ``ProfileFieldsOut`` and
    deliberately *not* hoisted onto the outer model: they are facts about the
    profile row, and an account with no profile has no such facts. Hoisting them
    would mean rendering two nulls whose nullness meant nothing, which is the
    shape this whole nesting exists to avoid.
    """
    profile = standing.profile
    return schemas.ProfileOut(
        user_id=standing.user_id,
        profile=(
            None
            if profile is None
            else schemas.ProfileFieldsOut(
                display_name=profile.display_name,
                legal_first_name=profile.legal_first_name,
                legal_last_name=profile.legal_last_name,
                date_of_birth=profile.date_of_birth,
                phone=profile.phone,
                country=profile.country,
                address_line=profile.address_line,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
        ),
        tier=standing.tier.value,
        is_complete=standing.is_complete,
        limits=tier_limits_out(standing.tier),
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


def profile_fields_in(payload: schemas.ProfileIn) -> dict:
    """A profile body as the keyword arguments ``ProfileService.save`` takes.

    **A dict rather than seven positional arguments at the route**, and that is
    the whole of this function. The service's signature and this model's fields
    are the same seven names, so ``**profile_fields_in(body)`` cannot transpose
    two of them - and transposing them is the failure mode a positional call
    invites, where ``legal_first_name`` and ``legal_last_name`` are both ``str |
    None`` and a swap produces a profile that is wrong in a way nothing rejects.

    Note what is *not* here: ``now``. The clock is read at the route, beside the
    ``datetime.now()`` every other route reads, so the moment this system thinks
    it is enters in one visible place per request rather than inside a
    translation.

    Nothing is validated, converted or defaulted on the way through. The values
    reach ``Profile.__post_init__`` exactly as pydantic parsed them, which is the
    arrangement ``SignUpIn.password`` argues for: the domain answers a bad value
    with the field's name in its own vocabulary, and a conversion here would be a
    second opinion about the same question.
    """
    return {
        "display_name": payload.display_name,
        "legal_first_name": payload.legal_first_name,
        "legal_last_name": payload.legal_last_name,
        "date_of_birth": payload.date_of_birth,
        "phone": payload.phone,
        "country": payload.country,
        "address_line": payload.address_line,
    }
