"""Command-line interface for Budget Manager.

The presentation layer's only job is to translate user intent into calls on the
application service and render the result. It touches the services built by the
composition root - never repositories, never the Unit of Work.

Run from the repo root:

    .venv/bin/python -m app.presentation.cli --db budget.db open --currency NGN
    .venv/bin/python -m app.presentation.cli --db budget.db deposit <wallet_id> 5000

    .venv/bin/python -m app.presentation.cli --db budget.db plan create \\
        --wallet <wallet_id> --name "Salary 2026" \\
        --source locked --every monthly --for 12 months \\
        --pay 20000 0123456789 058 "Chinedu Okafor" salary
    .venv/bin/python -m app.presentation.cli --db budget.db plan tick

Where validation happens is worth noticing, because the split is deliberate.
argparse checks *shape*: that a UUID parses, that a date is ISO, that a cadence
is one of the four words, that an amount arrived at all. It does not check
*meaning*, because meaning needs the wallet - amounts on this CLI are unitless
and are read in the wallet's currency, and a plan's currency has to match it.
So the amounts are converted only after the wallet is loaded, and the domain's
own errors are what the user sees. Those errors all derive from MoneyError,
which ``main`` already renders as ``error: ...`` with exit code 1, so a domain
rejection needs no translation layer here at all.

``plan tick`` also delivers any queued pre-payout warnings, but only if email is
configured. It is configured entirely from the environment, so that the SMTP
password never has to live in ``budget.db``:

    SMTP_HOST=smtp.example.com     # required; nothing is sent without it
    SMTP_PORT=587                  # default 587, the STARTTLS submission port
    SMTP_USER=me@example.com       # optional; omit for a server needing no login
    SMTP_PASSWORD=...              # optional, paired with SMTP_USER
    BUDGET_NOTIFY_TO=me@example.com    # required; where warnings are sent
    BUDGET_NOTIFY_FROM=me@example.com  # defaults to SMTP_USER
    SMTP_STARTTLS=0                # only for a local test server with no cert

An install with none of these set is not broken: the tick still warns, still
records the warning, and still pays - the warnings simply have nowhere to go.

Two kinds of message go out over that configuration, and they are *not* the same
thing at the same time:

    warning   an occurrence is coming, ~30 minutes before the run (``11:30``)
    receipt   an event happened, at the moment it happens (``12:00``, and the
              same second a deposit is typed at the terminal)

Both are drained by ``plan tick``; a wallet command drains its own receipt before
it exits, which is what "immediately" means here. Neither can stop a payment: the
money is committed before any socket is opened, so a mail server that is down or
slow costs latency and never correctness.
"""

import argparse
import sys
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from app.application.plan_service import PlanService
from app.application.wallet_service import WalletService
from app.composition_root import (
    build_deliverer,
    build_notification_deliverer,
    build_notifier,
    build_plan_service,
    build_scheduler,
    build_wallet_service,
)
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import InvalidAmountError, MoneyError
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.planning.cadence import Cadence
from app.domain.planning.duration import Duration
from app.domain.planning.durationUnit import DurationUnit
from app.domain.planning.exception import (
    InvalidDurationAmountError,
    InvalidDurationUnitError,
)
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planRun import PlanRun
from app.domain.planning.planSource import PlanSource
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.planning.schedule import Schedule
from app.infrastructure.notifications.email_settings import (
    describe_configuration,
    from_environment,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

#: Which operations move money, and the verb to report on success.
OPERATIONS = {
    "deposit": "deposited",
    "withdraw": "withdrew",
    "lock": "locked",
    "release": "released",
}


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid UUID: {value!r}")


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"invalid amount: {value!r}")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date (expected YYYY-MM-DD): {value!r}")


def _datetime(value: str) -> datetime:
    """A moment, from ``YYYY-MM-DDTHH:MM`` - or from a bare date, meaning midnight.

    The bare-date form is accepted deliberately. ``--from 2026-01-01`` is what
    existing habits and this project's own tests already say, and reading it as
    midnight is both the least surprising answer and exactly the answer the old
    date-based code gave. Accepting it also keeps the command line and the store
    telling the same story: both read a bare date as midnight.

    Note the asymmetry with ``--until``, which stays a plain date. A plan starts
    at a moment but ends on a day, so the two flags genuinely want two parsers.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid moment (expected YYYY-MM-DD or YYYY-MM-DDTHH:MM): {value!r}"
        )


def _moment(value: datetime) -> str:
    """Render a moment for display, to the minute.

    Seconds are dropped because nothing here sets them: a plan's moment comes
    from its anchor or from ``--as-of``, and both are typed by a human. Showing
    "12:00:00" would report a precision the value does not have.
    """
    return value.isoformat(timespec="minutes")


def _wait(until: timedelta) -> str:
    """Render a wait for display, in whole minutes and floored.

    Floored rather than rounded, and that direction is the point: a warning is
    only useful if the number it gives can be trusted, and rounding 29 minutes
    and 40 seconds up to "30 minutes" promises time that is not there. Rounding
    down is never wrong about how long is left.

    Minutes are the right unit for a wait measured in the tens of minutes; a
    window in seconds would need a different helper rather than a cleverer one.
    """
    minutes = int(until.total_seconds() // 60)
    unit = "minute" if minutes == 1 else "minutes"
    return f"{minutes} {unit}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="budget-manager",
        description="A personal savings wallet with deposit, withdraw, "
        "lock and release operations.",
    )
    parser.add_argument(
        "--db",
        default="budget.db",
        help="SQLite database file (default: budget.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open", help="open a new wallet")
    open_parser.add_argument(
        "--user",
        type=_uuid,
        help="owner user UUID (auto-generated if omitted)",
    )
    open_parser.add_argument(
        "--currency",
        required=True,
        choices=[currency.name for currency in Currency],
        help="wallet currency",
    )

    balance_parser = subparsers.add_parser(
        "balance", help="show a wallet's status and balances"
    )
    balance_parser.add_argument("wallet_id", type=_uuid)

    freeze_parser = subparsers.add_parser(
        "freeze", help="freeze a wallet (stops withdrawals)"
    )
    freeze_parser.add_argument("wallet_id", type=_uuid)

    unfreeze_parser = subparsers.add_parser(
        "unfreeze", help="return a frozen wallet to active"
    )
    unfreeze_parser.add_argument("wallet_id", type=_uuid)

    history_parser = subparsers.add_parser(
        "history", help="show a wallet's transaction ledger, oldest first"
    )
    history_parser.add_argument("wallet_id", type=_uuid)

    # Not in OPERATIONS below: a payout takes a destination as well as an
    # amount, and OPERATIONS drives the plain amount+--ref operations.
    payout_parser = subparsers.add_parser(
        "payout",
        help="send money from the locked balance to a bank account",
    )
    payout_parser.add_argument("wallet_id", type=_uuid)
    payout_parser.add_argument("amount", type=_decimal)
    payout_parser.add_argument(
        "--account", required=True, help="destination account number"
    )
    payout_parser.add_argument(
        "--bank-code", required=True, help="destination bank code, e.g. 058"
    )
    payout_parser.add_argument(
        "--name", required=True, help="destination account name"
    )
    payout_parser.add_argument(
        "--ref",
        help="idempotency key (auto-generated if omitted)",
    )

    for name, _ in OPERATIONS.items():
        op_parser = subparsers.add_parser(name, help=f"{name} money")
        op_parser.add_argument("wallet_id", type=_uuid)
        op_parser.add_argument("amount", type=_decimal)
        op_parser.add_argument(
            "--ref",
            help="idempotency key (auto-generated if omitted)",
        )

    _add_plan_commands(subparsers)

    return parser


def _add_plan_commands(subparsers) -> None:
    """The ``plan`` command group.

    Nested rather than flat (``create-plan``, ``list-plans``, ...) because plans
    have their own vocabulary of eight verbs, and eight hyphenated top-level
    commands would make the root help unreadable while hiding the fact that they
    belong together. ``plan`` is a noun here; every verb under it acts on plans.
    """
    plan_parser = subparsers.add_parser("plan", help="create and steer savings plans")
    plan_commands = plan_parser.add_subparsers(dest="plan_command", required=True)

    create_parser = plan_commands.add_parser(
        "create", help="create a plan against a wallet"
    )
    create_parser.add_argument("--wallet", type=_uuid, required=True)
    create_parser.add_argument(
        "--name",
        required=True,
        help="what to call the plan, e.g. 'Salary 2026'",
    )
    create_parser.add_argument(
        "--source",
        required=True,
        choices=[source.value for source in PlanSource],
        help="which balance the plan spends from",
    )
    create_parser.add_argument(
        "--every",
        required=True,
        choices=[cadence.value for cadence in Cadence],
        help="how often the plan repeats",
    )
    create_parser.add_argument(
        "--from",
        dest="start",
        type=_datetime,
        # Midnight today, not ``datetime.now()``. "Now" carries seconds and
        # microseconds, and every occurrence is derived from the anchor - so a
        # plan created at 14:37:22.814 would pay at 14:37:22.814 forever, and the
        # jitter would be baked into every idempotency key it ever writes. The
        # default deliberately reports a day rather than a moment of time.
        default=datetime.combine(date.today(), time.min),
        help="the moment of the first run, e.g. 2026-03-02T12:00 "
        "(default: today at midnight)",
    )

    # A plan ends in one of two ways: an explicit date, or a term counted from
    # the first run. They are mutually exclusive on purpose - accepting both
    # would leave the question "which one wins?", and whichever answer we picked
    # would be a rule the user could not see.
    plan_end = create_parser.add_mutually_exclusive_group()
    plan_end.add_argument(
        "--until",
        type=_date,
        help="the date the plan stops being due, e.g. 2026-07-02 - a whole day, "
        "so a run on that day still happens",
    )
    plan_end.add_argument(
        "--for",
        dest="term",
        nargs=2,
        metavar=("AMOUNT", "UNIT"),
        help="how long the plan lasts, in days/weeks/months/years, "
        "e.g. --for 12 months",
    )

    create_parser.add_argument(
        "--pay",
        nargs=5,
        action="append",
        metavar=("AMOUNT", "ACCOUNT", "BANK_CODE", "NAME", "LABEL"),
        help="a payout line, repeated once per destination account",
    )
    create_parser.add_argument(
        "--release",
        nargs=2,
        action="append",
        metavar=("AMOUNT", "LABEL"),
        help="a release-to-available line, repeated as needed",
    )

    list_parser = plan_commands.add_parser(
        "list", help="list a wallet's plans, oldest first"
    )
    list_parser.add_argument("wallet_id", type=_uuid)

    show_parser = plan_commands.add_parser(
        "show", help="show a plan, its lines and its run history"
    )
    show_parser.add_argument("plan_id", type=_uuid)

    edit_parser = plan_commands.add_parser(
        "edit", help="replace a plan's instruction lines"
    )
    edit_parser.add_argument("plan_id", type=_uuid)
    edit_parser.add_argument(
        "--pay",
        nargs=5,
        action="append",
        metavar=("AMOUNT", "ACCOUNT", "BANK_CODE", "NAME", "LABEL"),
        help="a payout line, repeated once per destination account",
    )
    edit_parser.add_argument(
        "--release",
        nargs=2,
        action="append",
        metavar=("AMOUNT", "LABEL"),
        help="a release-to-available line, repeated as needed",
    )

    for verb, help_text in (
        ("pause", "stop a plan being due, keeping its place in the schedule"),
        ("resume", "return a paused plan to service"),
        ("cancel", "end a plan for good"),
    ):
        steering_parser = plan_commands.add_parser(verb, help=help_text)
        steering_parser.add_argument("plan_id", type=_uuid)

    tick_parser = plan_commands.add_parser(
        "tick", help="run every due plan once (the scheduler entry point)"
    )
    tick_parser.add_argument(
        "--as-of",
        type=_datetime,
        default=datetime.combine(date.today(), time.min),
        help="the moment to treat as now, e.g. 2026-03-02T12:00 "
        "(default: today at midnight)",
    )


def _open(service: WalletService, args) -> int:
    user_id = args.user if args.user is not None else uuid.uuid4()
    wallet = service.open_wallet(user_id, Currency[args.currency])
    print(
        f"opened wallet {wallet.wallet_id} "
        f"(currency {wallet.currency.name})"
    )
    return 0


def _balance(service: WalletService, args) -> int:
    wallet: Wallet = service.get_wallet(args.wallet_id)
    print(f"status: {wallet.status.name.lower()}")
    print(f"available: {wallet.available_balance}")
    print(f"locked: {wallet.locked_balance}")
    return 0


def _deliver_after(factory, settings, deferred_reason=None) -> None:
    """Send the receipts this command just queued, before it exits.

    This is what "immediately" means, and it is the whole reason the wallet
    commands build a deliverer at all. The alternative - leaving receipts for the
    next ``plan tick`` to notice - would mean a deposit made at 14:03 is reported
    when the scheduler next runs, and an install with no scheduler running is
    reported never. An event that has happened is reported when it happens.

    **The money has already committed by the time this runs**, and that is what
    makes opening a socket here safe rather than reckless. A mail server that
    hangs costs this command latency and never correctness: the balance printed
    above this line is already durable, and a send that fails leaves the receipt
    queued rather than losing it. The asymmetry with ``plan tick`` is worth
    noticing - there, delivery is deferred to the end of an unattended job where
    a few seconds of waiting is invisible; here it happens in front of a person
    waiting for their prompt back. Ten seconds of that is an acceptable price for
    a receipt that arrives by itself; a minute would not be.

    Nothing at all is printed when there is nothing to send, which is the case on
    every unconfigured install - ``compose`` queues nothing without a recipient,
    so the queue is empty and ``_report_notifications`` stays silent.
    """
    deliverer = build_notification_deliverer(
        unit_of_work_factory=factory, settings=settings
    )
    _report_notifications(deliverer.execute(datetime.now()), deferred_reason)


def _operation(
    service: WalletService, args, factory, settings, deferred_reason
) -> int:
    # Resolve the wallet so the unitless amount is interpreted in its currency.
    wallet = service.get_wallet(args.wallet_id)
    amount = Money(args.amount, wallet.currency)
    internal_reference = args.ref if args.ref is not None else str(uuid.uuid4())

    method = getattr(service, args.command)
    method(args.wallet_id, amount, internal_reference)

    current = service.get_wallet(args.wallet_id)
    verb = OPERATIONS[args.command]
    print(
        f"{verb} {amount} | "
        f"available {current.available_balance} | "
        f"locked {current.locked_balance}"
    )
    # After the line above, and that order is deliberate: the result the user
    # asked for is printed first, so a slow mail server delays the delivery
    # report rather than the answer.
    _deliver_after(factory, settings, deferred_reason)
    return 0


def _payout(service: WalletService, args, factory, settings, deferred_reason) -> int:
    # Like every other amount on this CLI, the number carries no currency of its
    # own - it is read in the wallet's currency.
    wallet = service.get_wallet(args.wallet_id)
    amount = Money(args.amount, wallet.currency)
    destination = Destination(
        kind=DestinationKind.BANK_ACCOUNT,
        identifier=args.account,
        name=args.name,
        details={"bank_code": args.bank_code},
    )
    internal_reference = args.ref if args.ref is not None else str(uuid.uuid4())

    service.payout_from_locked(
        args.wallet_id, amount, internal_reference, destination
    )

    current = service.get_wallet(args.wallet_id)
    print(
        f"paid {amount} to {destination} | "
        f"available {current.available_balance} | "
        f"locked {current.locked_balance}"
    )
    _deliver_after(factory, settings, deferred_reason)
    return 0


def _freeze(service: WalletService, args) -> int:
    wallet = service.freeze_wallet(args.wallet_id)
    print(f"wallet {wallet.wallet_id} is now {wallet.status.name.lower()}")
    return 0


def _unfreeze(service: WalletService, args) -> int:
    wallet = service.unfreeze_wallet(args.wallet_id)
    print(f"wallet {wallet.wallet_id} is now {wallet.status.name.lower()}")
    return 0


def _history(service: WalletService, args) -> int:
    transactions = service.transactions_for_wallet(args.wallet_id)
    if not transactions:
        print(f"no transactions for wallet {args.wallet_id}")
        return 0
    for transaction in transactions:
        print(
            f"{transaction.created_at.isoformat(timespec='seconds')}  "
            f"{transaction.type.name.lower():<8} "
            f"{transaction.amount}  "
            f"{transaction.status.name.lower()}"
        )
    return 0


def _money(text: str, currency: Currency) -> Money:
    """Read a unitless CLI amount in the wallet's currency.

    The failure is raised as ``InvalidAmountError`` - a MoneyError - so ``main``
    renders it as a plain ``error: ...`` line instead of a traceback. That is
    also why the conversion does not happen in an argparse ``type=``: at parse
    time there is no currency to read the number in, because the currency comes
    from the wallet, which cannot be loaded until afterwards.
    """
    try:
        return Money(Decimal(text), currency)
    except InvalidOperation:
        raise InvalidAmountError(f"invalid amount: {text!r}")


def _lines(args, currency: Currency) -> tuple[Instruction, ...]:
    """Turn the parsed --pay/--release groups into domain Instructions.

    A tuple, because that is what ``SavingsPlan`` demands - the aggregate
    refuses a list so that nobody can hold a mutable reference to its lines and
    append past every invariant. Built in one place so that ``plan create`` and
    ``plan edit`` cannot drift apart in how they read a command line.

    Payouts come out before releases regardless of the order typed, since
    argparse hands the two flags back as separate lists. That is safe rather
    than merely convenient: a run is checked against its total before anything
    moves, so no ordering of the lines can leave the wallet half-spent.
    """
    instructions = []
    for amount, account, bank_code, name, label in args.pay or ():
        instructions.append(
            Instruction(
                action=PlannedAction.PAYOUT,
                amount=_money(amount, currency),
                label=label,
                destination=Destination(
                    kind=DestinationKind.BANK_ACCOUNT,
                    identifier=account,
                    name=name,
                    details={"bank_code": bank_code},
                ),
            )
        )
    for amount, label in args.release or ():
        instructions.append(
            Instruction(
                action=PlannedAction.RELEASE,
                amount=_money(amount, currency),
                label=label,
            )
        )
    return tuple(instructions)


def _end_date(args) -> date | None:
    """The plan's end date, from --until or from --for resolved against --from.

    ``--for`` is resolved *here*, into a concrete date, rather than being stored
    on the plan as a term. The plan records the date because that is the thing
    that stays true: a plan made "for 12 months" must answer "when does it end?"
    the same way a year later, and storing the term would mean re-resolving it
    against an anchor that is already doing that job. One source of truth.
    """
    if args.until is not None:
        return args.until
    if args.term is None:
        return None

    amount_text, unit_text = args.term
    try:
        amount = int(amount_text)
    except ValueError:
        raise InvalidDurationAmountError(
            f"the term must be a whole number of units, got {amount_text!r}"
        )
    try:
        unit = DurationUnit(unit_text)
    except ValueError:
        allowed = ", ".join(one.value for one in DurationUnit)
        raise InvalidDurationUnitError(
            f"the term unit must be one of {allowed}, got {unit_text!r}"
        )
    return Duration(amount, unit).end_from(args.start)


def _run_outcome(run: PlanRun) -> str:
    """Render a run's result, naming the block reason when there is one.

    The reason is the whole value of the row. "blocked" alone tells the user
    their plan stopped but not that topping the wallet up would restart it,
    which is the only part of the news they can act on.
    """
    if run.reason is None:
        return run.status.value
    return f"{run.status.value} ({run.reason.value})"


def _plan_command(
    args, factory, wallet_service: WalletService, settings, deferred_reason
) -> int:
    """Route a ``plan`` sub-command to its handler.

    The scheduler and the notifier are built only for ``tick``, and that is not
    just laziness: the scheduler wires two use cases onto one Unit of Work
    factory (see ``build_scheduler``), and constructing that for a command that
    will never run a plan would hide the fact that the sharing matters only
    there.

    ``settings`` and ``deferred_reason`` arrive from ``main`` rather than being
    read here. They used to be read in this branch, and they moved up with the
    receipts: a wallet command now delivers too, so there are two places that
    need to know how this invocation is configured, and two readers would mean a
    process could in principle act on two different configurations. It also keeps
    the promise ``email_settings`` makes - that ``os.environ`` is read in one
    module, by the CLI at one moment.
    """
    plan_service = build_plan_service(unit_of_work_factory=factory)
    if args.plan_command == "tick":
        return _plan_tick(
            args,
            build_scheduler(unit_of_work_factory=factory, settings=settings),
            build_notifier(unit_of_work_factory=factory, settings=settings),
            build_deliverer(unit_of_work_factory=factory, settings=settings),
            build_notification_deliverer(
                unit_of_work_factory=factory, settings=settings
            ),
            deferred_reason=deferred_reason,
        )
    if args.plan_command == "create":
        return _plan_create(args, plan_service, wallet_service)
    if args.plan_command == "list":
        return _plan_list(args, plan_service)
    if args.plan_command == "show":
        return _plan_show(args, plan_service)
    if args.plan_command == "edit":
        return _plan_edit(args, plan_service)
    return _plan_steer(args, plan_service)


def _plan_create(
    args, service: PlanService, wallet_service: WalletService
) -> int:
    # The wallet supplies the currency every bare number on this command is read
    # in, and loading it here has a useful side effect: an unknown wallet fails
    # before anything is built. Its *balance* is deliberately not consulted. A
    # plan may exist before it is affordable - that is what saving towards one
    # means - and the shortfall is reported when a run actually fires.
    wallet = wallet_service.get_wallet(args.wallet)
    plan = service.create_plan(
        wallet_id=args.wallet,
        name=args.name,
        source=PlanSource(args.source),
        schedule=Schedule(cadence=Cadence(args.every), anchor=args.start),
        instructions=_lines(args, wallet.currency),
        ends_on=_end_date(args),
    )
    print(f"created {plan}")
    return 0


def _plan_list(args, service: PlanService) -> int:
    plans = service.plans_for_wallet(args.wallet_id)
    if not plans:
        print(f"no plans for wallet {args.wallet_id}")
        return 0
    for plan in plans:
        marker = "  (irreversible)" if plan.is_irreversible else ""
        # The *full* plan_id, not a shortened one. A truncated id looks tidier
        # and is useless: every command that takes a plan id parses a whole
        # UUID, so the only thing a user can do with eight characters is fail to
        # paste them back. This matches how `open` reports a wallet id.
        print(
            f"{plan.plan_id}  {plan.name:<20}  "
            f"{plan.status.value:<9}  next {_moment(plan.next_due_at)}  "
            f"{plan.total_to_move}{marker}"
        )
    return 0


def _plan_show(args, service: PlanService) -> int:
    plan = service.get_plan(args.plan_id)
    print(f"name: {plan.name}")
    print(f"id: {plan.plan_id}")
    print(f"status: {plan.status.value}")
    print(f"source: {plan.source.value}")
    print(f"schedule: {plan.schedule}")
    print(f"next due: {_moment(plan.next_due_at)}")
    # A bare date, deliberately, while the two lines above and below show
    # moments. That is the product rule made visible rather than an oversight: a
    # plan starts at an instant and ends on a *day*, so "ends on: 2026-07-02"
    # means through all of 2 July. Dressing it up as a moment would suggest the
    # plan stops at midnight, which is the reading decision this exists to avoid.
    print(f"ends on: {plan.ends_on.isoformat() if plan.ends_on else 'never'}")
    print(f"runs completed: {plan.completed_runs}")
    # Said outright rather than left for the user to infer from the lines. It is
    # the one property of a plan that stops the usual remedies working, and
    # finding that out by having `plan cancel` refused is a poor way to learn it.
    print(f"can be cancelled: {'no' if plan.is_irreversible else 'yes'}")

    print("lines:")
    for index, instruction in enumerate(plan.instructions, start=1):
        print(f"  {index}. {instruction}")

    runs = service.runs_for_plan(args.plan_id)
    print("runs:")
    if not runs:
        print("  (none yet)")
    for run in runs:
        print(f"  {_moment(run.due_at)}  {_run_outcome(run)}")
    return 0


def _plan_edit(args, service: PlanService) -> int:
    # The currency comes from the plan rather than from a --wallet argument. A
    # plan's currency cannot change - the aggregate allows only one across its
    # lines, and the use case holds it equal to the wallet's - so the plan is
    # the authority on how to read these numbers.
    plan = service.get_plan(args.plan_id)
    updated = service.edit_instructions(
        args.plan_id, _lines(args, plan.total_to_move.currency)
    )
    print(f"updated {updated}")
    return 0


def _plan_steer(args, service: PlanService) -> int:
    # pause, resume and cancel are the same shape from here: a plan id in, a
    # new status out. The differences that matter - which statuses each accepts,
    # and that a release plan refuses to be cancelled - belong to the aggregate,
    # and it is the aggregate's error the user sees.
    method = getattr(service, f"{args.plan_command}_plan")
    plan = method(args.plan_id)
    print(f"plan {plan.plan_id} is now {plan.status.value}")
    return 0


def _plan_tick(
    args, scheduler, notifier, deliverer, notification_deliverer, deferred_reason=None
) -> int:
    """Warn about plans that are nearly due, run the ones that are, then deliver.

    This is the whole of "automated" at this layer: one command that does one
    pass. Pairing it with cron (or systemd, or a loop) is what turns it into a
    schedule, and doing it that way keeps the process short-lived and its
    failures visible, instead of hiding them in a daemon nobody watches.

    Three steps, and the order is the only thing tying them together. They are
    otherwise independent: a plan is either coming up or due, never both, so the
    warning and the runs in practice name different plans - and nothing about
    the warning can affect whether a payout happens. Both use cases are invoked
    unconditionally, which is what keeps the courtesy a courtesy.

    **Delivery runs last**, after the money has moved, and that is defence in
    depth rather than necessity. A warning tick and a payout tick are never the
    same invocation, so a slow mail server could not delay a payout anyway -
    but network I/O should sit as far from a payment as it can be made to, and
    putting it at the end costs nothing at all. Saying so is cheap; relying on
    it not being true later is not.

    **Last is also what makes the receipt immediate.** The runs happen above, and
    each one queues its receipt inside its own transaction; draining at the end
    therefore sends the receipt for a payout made seconds ago, in the same
    invocation. Draining *before* the runs would defer every receipt to the next
    tick, which for a tick that runs every five minutes is five minutes of a user
    not knowing their money left.
    """
    raised = notifier.execute(args.as_of)
    for notice in raised:
        # The full plan id, for the same reason `plan list` and the run lines
        # below carry it: the actionable next step is pasting it into `plan show`
        # to see what this warning is actually about.
        print(
            f"warning: {notice.plan_id}  {_moment(notice.due_at)}  "
            f"{notice.plan_name!r} pays {notice.amount} "
            f"in {_wait(notice.due_at - notice.raised_at)}"
        )
    if raised and deferred_reason is not None:
        # There was something to say and nowhere to send it. Worth one line,
        # because the alternative is a user who reads `warning:` in the log
        # every month and never learns that these could reach their phone.
        #
        # Printed only when a warning was actually raised, so a quiet tick stays
        # quiet - and phrased as "only in this log" rather than "queued", because
        # nothing was queued: with no address there is no message, which is the
        # same reason the queue below stays empty.
        print(
            f"note: this warning is only in this log - "
            f"{deferred_reason} (see 'Running the scheduler' in the README)"
        )

    runs = scheduler.execute(args.as_of)
    if not runs:
        print(f"nothing due as of {_moment(args.as_of)}")
    else:
        for run in runs:
            # Full id again, for the same reason as `plan list`: the actionable
            # part of a blocked line is that you can paste the id straight into
            # `plan show` to find out what happened.
            print(f"{run.plan_id}  {_moment(run.due_at)}  {_run_outcome(run)}")

    # Deliberately not gated on anything above: the queues are drained whether
    # or not this tick found a run, and whether or not the notifier raised
    # anything new. A message queued by an earlier tick is owed regardless of
    # what this one did.
    #
    # Two drains, warnings first, and they are separate calls to separate classes
    # rather than one pass over both queues - see ``DeliverNotifications`` for
    # why. What they share is the reporting rule: say what happened, and stay
    # silent when nothing did.
    _report_delivery(deliverer.execute(args.as_of), deferred_reason)
    _report_notifications(notification_deliverer.execute(args.as_of), deferred_reason)
    return 0


def _report_delivery(report, deferred_reason=None) -> None:
    """Print one line per message this pass touched, and nothing when there is none.

    A quiet pass is the ordinary case - most ticks find an empty queue - so
    printing "nothing to deliver" would fill the log with the one line that
    carries no information. Every line here is something that happened.

    ``failed`` and ``deferred`` repeat on every tick until they clear, which is
    intended: a message stuck behind a dead mail server should be visible in the
    log every five minutes until someone fixes it. Silence there is precisely the
    failure this feature exists to make impossible.

    ``deferred`` is rarer than it looks and is worth naming, because the two
    states are easy to confuse. A message is deferred when it is queued *and*
    there is no channel - so it can only happen to a message queued while email
    was configured and still owed after the configuration went away (the send
    failed, or the tick was killed before the drain). A fresh unconfigured
    install has no recipient, composes no message, and so has nothing to defer;
    that case prints the ``note`` in ``_plan_tick`` instead.

    ``deferred_reason`` names the environment variable that is missing, when the
    CLI knows it. "queued" on its own is a line the reader has to go and
    investigate; "queued (email not configured: SMTP_HOST is not set)" is a
    two-minute fix. That sentence is the entire reason
    ``describe_configuration`` exists separately from ``from_environment``.
    """
    for message in report.sent:
        print(
            f"emailed {message.plan_id}  {_moment(message.due_at)}  "
            f"to {message.recipient}"
        )
    for message in report.expired:
        # Not an error: the occurrence came and went before the message could be
        # sent, and a warning about it would now be wrong rather than late.
        print(
            f"expired {message.plan_id}  {_moment(message.due_at)}  "
            f"(occurrence passed before it could be sent)"
        )
    for message in report.failed:
        print(
            f"failed  {message.plan_id}  {_moment(message.due_at)}  "
            f"{message.last_error} (will retry)"
        )
    if report.deferred:
        reason = deferred_reason or "email not configured"
        for message in report.deferred:
            print(
                f"queued  {message.plan_id}  {_moment(message.due_at)}  "
                f"({reason})"
            )


def _report_notifications(report, deferred_reason=None) -> None:
    """Print one line per receipt this pass touched, and nothing when there is none.

    The sibling of ``_report_delivery``, and a separate function because the two
    messages have genuinely different shapes. A warning knows the plan and the
    occurrence it announces; a receipt knows a *kind* and the plan or wallet it
    is about. One function serving both would have to decide which fields exist
    from the type of the message, which is exactly the kind of branch that grows
    a wrong answer the next time a kind is added.

    Note the line says the kind - ``payout_succeeded``, ``wallet_deposit`` -
    rather than a sentence. It is a log, not the message: the words that went to
    the user are in the row, and repeating them here would make a log nobody can
    scan. The kind is also what the ``notifications`` table is queried by, so the
    log and the diagnostic answer the same question.

    **There is no ``expired`` branch, and that is not an omission.** Nothing
    expires a notification - see ``DeliverNotifications`` - so the line could
    never print. Leaving it out is what keeps that true: a dead branch in a
    presenter is how a rule nobody implements acquires the appearance of one, and
    the next reader would reasonably assume something was supposed to expire
    these.
    """
    for notification in report.sent:
        print(
            f"emailed {notification.kind.value}  {notification.subject_id}  "
            f"to {notification.recipient}"
        )
    for notification in report.failed:
        print(
            f"failed  {notification.kind.value}  {notification.subject_id}  "
            f"{notification.last_error} (will retry)"
        )
    if report.deferred:
        reason = deferred_reason or "email not configured"
        for notification in report.deferred:
            print(
                f"queued  {notification.kind.value}  {notification.subject_id}  "
                f"({reason})"
            )


def _describe(exc: MoneyError) -> str:
    return str(exc) if str(exc) else exc.__class__.__name__


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # One factory for the whole invocation. Every service built from it opens
    # its own unit per business operation, which is the intended transaction
    # boundary - sharing the factory is not the same as sharing a transaction.
    factory = SqliteUnitOfWorkFactory(args.db)
    # One read of the environment for the whole invocation, handed to whatever
    # needs it. The notifier needs the address to compose with, the deliverers
    # need the credentials to send with, and a wallet command needs both to
    # deliver its own receipt - so this is read here once rather than in each of
    # those places. Reading it per-use-case would let one invocation act on two
    # configurations, which is a state nothing would report.
    settings = from_environment()
    # Why there is no email, if there is none. Computed here because it is a fact
    # about *this* installation's environment, and the deliverers - which only
    # see messages - have no way to know it.
    deferred_reason = describe_configuration() if settings is None else None
    service = build_wallet_service(
        unit_of_work_factory=factory, settings=settings
    )
    try:
        if args.command == "plan":
            return _plan_command(
                args, factory, service, settings, deferred_reason
            )
        if args.command == "open":
            return _open(service, args)
        if args.command == "balance":
            return _balance(service, args)
        if args.command == "freeze":
            return _freeze(service, args)
        if args.command == "unfreeze":
            return _unfreeze(service, args)
        if args.command == "history":
            return _history(service, args)
        if args.command == "payout":
            return _payout(service, args, factory, settings, deferred_reason)
        return _operation(service, args, factory, settings, deferred_reason)
    except MoneyError as exc:
        print(f"error: {_describe(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
