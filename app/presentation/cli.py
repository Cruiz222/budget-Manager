"""Command-line interface for Budget Manager.

The presentation layer's only job is to translate user intent into calls on the
application service and render the result. It touches the services built by the
composition root - never repositories, never the Unit of Work.

**Every wallet command runs as somebody, and that somebody is proved by a
token.** The CLI is a client of its own accounts rather than a privileged
operator of them: it registers with ``signup``, obtains a token with ``login``,
keeps that token in a file, and presents it on every command that reads or moves
money. There is no flag that names a user, because a flag would be an assertion
and an assertion is what a session replaced.

    .venv/bin/python -m app.presentation.cli --db budget.db signup ada@example.com
    .venv/bin/python -m app.presentation.cli --db budget.db login ada@example.com
    .venv/bin/python -m app.presentation.cli --db budget.db open --currency NGN
    .venv/bin/python -m app.presentation.cli --db budget.db deposit <wallet_id> 5000
    .venv/bin/python -m app.presentation.cli --db budget.db logout

The password is asked for, never passed as an argument: an argument lands in the
shell's history file and in ``ps`` output where anyone on the machine can read
it. Nothing else about the token is hidden - it lives in a file the user owns,
at ``--session``, defaulting to ``$BUDGET_SESSION`` or
``~/.config/budget/session``, created ``0600``.

``plan tick`` is the one command that runs as nobody, and it is worth knowing
why: it serves every user with a plan, so there is no person it could act as, and
a scheduler that needed one would need a privileged account to run under - which
is exactly the seat this arrangement removes. It therefore works with no session
at all, on a machine where nobody has ever logged in.

    .venv/bin/python -m app.presentation.cli --db budget.db plan create \\
        --wallet <wallet_id> --name "Salary 2026" \\
        --source locked --from-fund Payroll \\
        --every monthly --for 12 months \\
        --pay 20000 0123456789 058 "Chinedu Okafor" salary
    .venv/bin/python -m app.presentation.cli --db budget.db plan tick

A plan that spends the locked balance names the pot it draws from, because "the
locked balance" is a sum over several pots and cannot say where an amount was
meant to go:

    --source locked     --from-fund NAME is required
    --source available  --from-fund is refused; there is no pot to draw on

Reserving money means naming a pot too:

    # a pot that opens for release on a date, and takes deposits until then
    .venv/bin/python -m app.presentation.cli --db budget.db fund open \\
        --wallet <wallet_id> --name Vacation --kind personal --matures 2026-06-01
    .venv/bin/python -m app.presentation.cli --db budget.db fund deposit \\
        <wallet_id> Vacation 5000       # money in from outside
    .venv/bin/python -m app.presentation.cli --db budget.db fund lock \\
        <wallet_id> Vacation 5000       # available -> pot
    .venv/bin/python -m app.presentation.cli --db budget.db fund release \\
        <wallet_id> Vacation 5000       # refused until 2026-06-01
    .venv/bin/python -m app.presentation.cli --db budget.db fund list <wallet_id>

A business pot is the one kind that may pay a *scheduled* payout before its date,
and only when the commitment predates the money. The order is the whole rule, and
it is why a pot is opened empty, committed to, and funded last:

    fund open --kind business --matures 2026-12-01 --name Payroll
    plan create --source locked --from-fund Payroll ...      # the commitment
    fund deposit <wallet_id> Payroll 500000                  # then the money

Fund the pot first and the plan will block when it runs - the money has no
commitment that predates it, so it waits for the date like any other. Pushing the
date later with ``fund extend`` re-seals the pot, and a committed plan blocks
until the new date. An ad-hoc ``payout --fund Payroll`` is never scheduled, so it
is refused before maturity however the pot was funded.

``deposit`` and ``lock`` are the same two verbs the root used to carry, and the
distinction between them is unchanged - a deposit brings money in, a lock moves
money already in the wallet. What changed is that both name a pot. The root's
bare ``lock`` and ``release`` are gone rather than renamed, because an amount
with no pot cannot be acted on once pots are what the locked balance *is*.

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
import getpass
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from app.application.plan_service import PlanService
from app.application.wallet_service import WalletService
from app.composition_root import (
    build_deliverer,
    build_log_in,
    build_log_out,
    build_notification_deliverer,
    build_notifier,
    build_plan_service,
    build_resolve_actor,
    build_scheduler,
    build_sign_up,
    build_wallet_service,
)
from app.domain.identity.user import User
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import InvalidAmountError, MoneyError
from app.domain.money.fundKind import FundKind
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
from app.infrastructure.settings import (
    DEFAULT_DATABASE_PATH,
    describe_configuration,
    from_environment,
    session_path as configured_session_path,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

#: Which operations move money, and the verb to report on success.
#:
#: ``lock`` and ``release`` used to be here and are gone rather than renamed:
#: once money is reserved in *named* pots, "lock 5000" does not say which pot to
#: put it in, so there is no honest way to run it. Both moved under ``fund``,
#: where the pot is named - see ``_add_fund_commands``.
OPERATIONS = {
    "deposit": "deposited",
    "withdraw": "withdrew",
}

#: The ``fund`` verbs that actually move money, and how to run each.
#:
#: Maps the sub-command to the service method and the verb to report it with.
#: Every one of them is now handed the moment it runs at, where a ``needs_moment``
#: flag used to sit on ``release`` alone. That flag went because the reason it
#: existed stopped being particular to one verb: ``release`` consults the moment
#: to decide whether the pot has come due, and ``deposit`` and ``lock`` now stamp
#: it onto the pot as its ``first_funded_at`` - the moment a commitment has to
#: predate for a business pot to be allowed to pay early. Three verbs, one
#: argument, so a flag deciding who gets it would only be a way to get it wrong.
#:
#: ``open``, ``extend`` and ``list`` are deliberately absent - they move no
#: money, so they have no amount, no idempotency reference and nothing to report
#: in a balance line. They are handled on their own below.
_FUND_MONEY = {
    "deposit": ("deposit_into_fund", "deposited into"),
    "lock": ("lock_into_fund", "locked into"),
    "release": ("release_from_fund", "released from"),
}

#: Who the CLI acts as when nobody says otherwise.
#:
#: **There is no such constant any more, and its absence is the phase.** This
#: used to hold ``DEV_USER_EMAIL``, an address every invocation acted as unless
#: ``--user`` named another one - a shim that made the single-user install work
#: while ownership rules landed. It is gone along with the ``--user`` flag, and
#: what replaced it is not a better default: it is the requirement that a person
#: prove who they are before any command that touches money or reads a balance
#: will run. ``plan tick`` is the only command that acts as nobody, and it is
#: handled by never resolving an actor at all rather than by inventing one.
#:
#: Leaving a default here would have been the whole bypass in one line: every
#: command on every machine would authenticate as the same account, and it would
#: look like convenience rather than a hole.


class CliError(Exception):
    """A refusal this presentation makes, with no domain opinion behind it.

    Deliberately *not* a ``MoneyError``, for the reason ``errors.ApiError``
    gives on the HTTP side: the domain has no view about where a token file
    lives, whether it exists, or who can read it. Deriving from the money root to
    reuse its handler would put a filesystem concern inside the domain's
    exception tree, which is the one place this codebase keeps such things out
    of.

    It exists at all because ``main`` needs one place to turn a refusal into
    ``error: ...`` and exit 1, and a second root is cheaper than a message that
    pretends to be about money. The two presentations now have the same
    arrangement - a domain root and a presentation root, both caught at the top -
    which is a coincidence worth having rather than one worth engineering.
    """

    pass


class NotSignedInError(CliError):
    """There is no usable token at the session path, so no command can act.

    One class for "the file is missing", "it cannot be read" and "it is empty",
    because all three leave the user with the same next move and the message
    names which one happened. Telling them apart in the *type* would give
    ``main`` three branches that do the same thing.
    """

    pass


def _read_token(path: str) -> str:
    """The token at ``path``, or a refusal naming what is wrong with it.

    Read as plain text and stripped, because the file holds nothing else - see
    ``_write_token``. There is no format to parse and deliberately so: a file
    with fields in it invites a reader to trust one of them, and the only field
    that would be worth trusting here is the token, which is the one thing that
    cannot be checked locally anyway. Everything about whether this token is
    *good* is answered by the store, on the next command that uses it.

    The refusals are ``NotSignedInError`` rather than an unhandled ``OSError``,
    and the difference is what the user sees: a traceback with a path in it is
    what a bug looks like, and this is not a bug - it is the ordinary state of a
    machine where nobody has logged in yet.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            token = handle.read().strip()
    except FileNotFoundError:
        raise NotSignedInError(
            f"not signed in: no session at {path} - run 'login' first"
        )
    except OSError as exc:
        raise NotSignedInError(f"could not read the session at {path}: {exc}")

    if not token:
        raise NotSignedInError(
            f"not signed in: the session at {path} is empty - run 'login'"
        )
    return token


def _write_token(path: str, token: str) -> None:
    """Store ``token`` at ``path``, readable by its owner and nobody else.

    **Created with the mode already set, not written and then chmodded.** The
    two-step version leaves a window - between the ``open`` and the ``chmod`` -
    in which the file exists with whatever the umask allows, and a token that is
    world-readable for ten milliseconds on a shared machine is a token that was
    world-readable. ``os.open`` takes the mode as an argument, so the file is
    born private.

    The mode is subject to the umask, which can only ever *remove* bits - so the
    result is at most ``0600`` and possibly stricter. That asymmetry is the
    reason this is the safe direction to be wrong in.

    The parent directory is created if it is missing, because the default lives
    under ``~/.config`` and a fresh machine may not have that directory. It is
    created with the default mode rather than ``0700``: the token file's own mode
    is what protects the token, and a directory is not a place to be clever about
    permissions the rest of the user's config directory does not use.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # A trailing newline, so the file is a well-formed text file rather than a
    # 43-character line with no end - which matters for anything that ever reads
    # it, including a person running `cat` to find out whether they are logged in.
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")


def _forget_token(path: str) -> bool:
    """Delete the session file. Returns whether one was there to delete.

    The return value is for the *report*, not for control flow: signing out on a
    machine that was not signed in is not an error, and both paths end the same
    way. It is worth a line of output telling them apart anyway, because "logged
    out" after a command that found no session is mildly confusing, and the
    difference costs one boolean.
    """
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


def _prompt_password(confirm: bool) -> str:
    """Ask for a password, twice when it is being chosen rather than presented.

    ``getpass`` and never an argument, which is the whole reason this function
    exists: ``--password hunter2`` lands in the shell's history file, in the
    process table while the command runs, and in whatever the terminal is
    recording. A password typed at a prompt invisible to over-the-shoulder
    reading is the best a terminal offers.

    The confirmation is for ``signup`` and not for ``login``, and the asymmetry
    is deliberate rather than an oversight. A mistyped password at sign-up
    creates an account whose password nobody knows - including its owner, who
    will discover it at the login prompt and have no recourse. A mistyped
    password at login simply does not match, which is a refusal the user
    understands immediately and can retry. Confirming the second would be a
    second invisible typing of a secret that is about to be checked anyway.

    An empty password is *not* refused here. ``PlainPassword`` owns that rule -
    including the minimum length - and a second check would be a rule free to
    disagree with it. The mismatch case is refused here, because "you typed two
    different things" is a fact about this prompt and not about passwords.
    """
    password = getpass.getpass("password: ")
    if not confirm:
        return password
    again = getpass.getpass("password again: ")
    if password != again:
        raise CliError("the two passwords did not match")
    return password


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
        description="A personal savings wallet with deposit, withdraw and "
        "named locked pots (fund open/deposit/lock/release/extend/list).",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database file (default: {DEFAULT_DATABASE_PATH})",
    )
    # Where the token lives, defaulted rather than read here.
    #
    # ``configured_session_path()` is called at parse time, which is the one
    # moment ``os.environ`` is read for this setting - so it is read exactly
    # once, by ``app.infrastructure.settings``, exactly as ``--db``'s own default
    # is a constant that module owns. Reading ``os.environ`` here would be the
    # second reader of the environment this codebase has been keeping out, and it
    # would be the one that made the promise false.
    #
    # Unlike ``--db`` there is no unlogged-in default path that works, and that
    # is not a gap: a token cannot be defaulted the way a filename can, because a
    # default token would be a token every install shares.
    parser.add_argument(
        "--session",
        default=configured_session_path(),
        metavar="PATH",
        help="file holding the session token (default: $BUDGET_SESSION, else "
        "~/.config/budget/session)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- identity: the three commands that need no session -------------------
    #
    # They are top-level rather than nested under a noun, because there is no
    # noun that covers them - ``account signup`` would suggest the other two act
    # on an account too, and logging out discards a token rather than touching
    # one. Three verbs, no group.
    signup_parser = subparsers.add_parser(
        "signup",
        help="register an address, with a password to prove it later",
    )
    signup_parser.add_argument("email", help="the address to register")
    # No --password argument, and its absence is deliberate. See
    # ``_prompt_password``: an argument lands in the history file.
    signup_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="ask for the password once instead of twice (for scripts that "
        "generate it, where a second typing proves nothing)",
    )

    login_parser = subparsers.add_parser(
        "login", help="obtain a session token and store it at --session"
    )
    login_parser.add_argument("email", help="the address to sign in as")

    subparsers.add_parser(
        "logout", help="discard the stored token and end the session"
    )

    open_parser = subparsers.add_parser("open", help="open a new wallet")
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

    # Reads nothing and moves nothing - it prints the identity every other
    # command is about to act as. It used to be here to make the dev shim
    # visible: ``--user`` created accounts nobody could inspect, so "whose is
    # this wallet?" needed a command that answered it. The shim is gone and the
    # command earned its place anyway, for a better reason - it is the cheapest
    # way to ask whether the token on this machine still works, and to find out
    # which account it belongs to when the answer is yes.
    subparsers.add_parser(
        "whoami", help="show the user this invocation acts as"
    )

    # Not in OPERATIONS below: a payout takes a destination as well as an
    # amount, and OPERATIONS drives the plain amount+--ref operations.
    payout_parser = subparsers.add_parser(
        "payout",
        help="send money from the locked balance to a bank account",
    )
    payout_parser.add_argument("wallet_id", type=_uuid)
    payout_parser.add_argument("amount", type=_decimal)
    payout_parser.add_argument(
        "--fund",
        help="the pot to draw on, by name - omit to spend the matured pots "
        "oldest first, which is how a plan saved before pots could be named "
        "is paid",
    )
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
    _add_fund_commands(subparsers)

    return parser


def _add_fund_commands(subparsers) -> None:
    """The ``fund`` command group - the wallet's named pots.

    Nested for the same reason ``plan`` is: six verbs that all act on one noun,
    and six more hyphenated top-level commands would bury the four that act on
    the wallet itself. ``fund`` is that noun's name.

    The two verbs worth pausing on are ``deposit`` and ``lock``, because they are
    the *same two words* the top level used to have and they do not mean the same
    thing here. ``deposit`` brings money in from outside; ``lock`` moves money
    that is already in the wallet. That distinction is unchanged - what changed
    is that both now name the pot they act on, which is the whole reason they
    moved. ``fund deposit`` and ``fund lock`` are also the difference between a
    pot that grows from outside and one that grows from the available balance,
    and the ledger records them as different types for that reason.
    """
    fund_parser = subparsers.add_parser(
        "fund", help="open named pots and move money in and out of them"
    )
    fund_commands = fund_parser.add_subparsers(dest="fund_command", required=True)

    open_parser = fund_commands.add_parser(
        "open", help="open a named pot on a wallet"
    )
    open_parser.add_argument("--wallet", type=_uuid, required=True)
    open_parser.add_argument(
        "--name",
        required=True,
        help="what to call the pot, e.g. 'Vacation' - unique within the wallet",
    )
    open_parser.add_argument(
        "--kind",
        required=True,
        choices=[kind.value for kind in FundKind],
        help="personal (release blocked until maturity) or business "
        "(also payable to an external account before maturity)",
    )
    open_parser.add_argument(
        "--matures",
        type=_date,
        help="the date the pot opens for release, e.g. 2026-06-01 - a whole "
        "day, so a release on that day is allowed; omit for a pot that is "
        "never sealed",
    )

    # deposit, lock, release: the same shape as each other and as the top-level
    # deposit/withdraw - wallet, pot, amount, optional reference.
    for verb, help_text in (
        ("deposit", "bring money in from outside, straight into a pot"),
        ("lock", "move money from the available balance into a pot"),
        ("release", "move money out of a pot, back to the available balance "
                    "(refused before the pot matures)"),
    ):
        money_parser = fund_commands.add_parser(verb, help=help_text)
        money_parser.add_argument("wallet_id", type=_uuid)
        money_parser.add_argument("fund_name", help="the pot's name")
        money_parser.add_argument("amount", type=_decimal)
        money_parser.add_argument(
            "--ref",
            help="idempotency key (auto-generated if omitted)",
        )

    extend_parser = fund_commands.add_parser(
        "extend", help="push a pot's maturity date later (never earlier)"
    )
    extend_parser.add_argument("wallet_id", type=_uuid)
    extend_parser.add_argument("fund_name", help="the pot's name")
    extend_parser.add_argument(
        "--to",
        type=_date,
        required=True,
        help="the new maturity date, which must be in the future and later "
        "than the current one",
    )

    list_parser = fund_commands.add_parser("list", help="list a wallet's pots")
    list_parser.add_argument("wallet_id", type=_uuid)


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
        "--from-fund",
        metavar="NAME",
        help="the pot the plan draws on, by name - required when --source is "
        "locked, refused when it is available",
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
    # Note what is deliberately *absent*: there is no ``--from-fund`` here, so
    # the pot is fixed for a plan's whole life. Changing it would point an
    # existing commitment at a different pot - the redirect the whole
    # anti-temptation rule exists to stop - and it would arrive through the one
    # door that rule cannot see, because the *new* pot would be judged against
    # the plan's *old* creation moment. A pot funded last week and named today
    # would look like a pot funded after a commitment made last month.
    #
    # The refusal is the absence of a flag rather than an error message, which
    # is a real choice: there is no arrangement of arguments to write, so there
    # is nothing to explain at the moment of failure. Getting it wrong is
    # recoverable anyway - ``plan cancel`` frees nothing, so the money stays
    # sealed to its own date and a new plan can be made against it.
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


def _signup(args, factory) -> int:
    """Register an address. Deliberately does not sign in afterwards.

    It would be easy to log the new account in here - the password is in hand,
    and every service the next command needs could be built immediately. It is
    not done, and the reason is the phase's thesis rather than a missing
    convenience: creating an identity and *proving* you hold it are separate acts,
    and the whole of the guarantee this CLI now offers rests on them staying
    separate. A registration that silently authenticated would mean the first
    session on a machine came from a command that never checked a password.

    The password is asked for twice by default. See ``_prompt_password``.
    """
    user = build_sign_up(unit_of_work_factory=factory).execute(
        args.email,
        _prompt_password(confirm=not args.no_confirm),
        datetime.now(),
    )
    print(f"registered {user.email} ({user.user_id})")
    print("next: run 'login' to start a session")
    return 0


def _login(args, factory) -> int:
    """Obtain a token and put it where every later command will look for it.

    **This is the only command that writes the session file**, which is worth
    knowing when something goes wrong with the CLI's identity: there is exactly
    one place a token can come from, and it is a password typed at a prompt.

    Overwriting an existing session is not refused. Logging in as somebody else
    while already signed in is a normal thing to want, and the alternative - a
    ``logout`` first - would mean two commands to do one thing, with a state in
    between where the machine is signed in as nobody. The old session is left
    alone on the server, which is the same shape ``LogIn`` describes for two
    simultaneous logins: it expires on its own, and nothing here decides that a
    person only has one device.
    """
    logged_in = build_log_in(unit_of_work_factory=factory).execute(
        args.email, _prompt_password(confirm=False), datetime.now()
    )
    _write_token(args.session, logged_in.token)
    print(
        f"logged in as {logged_in.user.email} | "
        f"expires {_moment(logged_in.session.expires_at)} | "
        f"token stored at {args.session}"
    )
    return 0


def _logout(args, factory) -> int:
    """End the session, on the server and on this machine.

    **Server first, then the file**, and the order is the whole of the safety
    here. Deleting the file first would mean a failure in between leaves a token
    that still works with no copy of it anywhere - the session is not ended and
    the user cannot end it, because the thing that names it is gone. This way a
    failure leaves the token in place and the command can simply be run again.

    A machine that is not signed in is not an error. The postcondition is "there
    is no usable session at this path", and it is already true - the same
    idempotence ``LogOut`` argues for one layer down, arriving here as an exit
    code of 0.
    """
    try:
        token = _read_token(args.session)
    except NotSignedInError as exc:
        # The message already says what is wrong and what to do about it, so it
        # is printed as the note it is rather than reworded into a generic one.
        print(str(exc))
        return 0

    build_log_out(unit_of_work_factory=factory).execute(token)
    _forget_token(args.session)
    print(f"logged out | session at {args.session} discarded")
    return 0


def _current_actor(args, factory) -> User:
    """The user this invocation acts as, proved by the token at ``--session``.

    The CLI's half of what ``dependencies.current_actor`` does for a request, and
    the same two steps: read the credential from where this presentation keeps
    it, then hand it to the one use case that turns one into an identity. The
    *rule* about what a token means - which hash, what counts as expired - is in
    the domain behind that use case, so the two presentations cannot disagree
    about it.

    ``datetime.now()`` is read at this line, following the API's boundary: this
    is where the wall clock enters, and everything underneath compares against a
    moment it was handed.

    It raises ``NotSignedInError`` rather than returning ``None``, because every
    caller of this function needs a user and none of them has a sensible
    alternative to offer. A caller that had one would ask ``_read_token``
    directly.
    """
    return build_resolve_actor(unit_of_work_factory=factory).execute(
        _read_token(args.session), datetime.now()
    )


def _open(service: WalletService, args) -> int:
    # The owner is not passed, and cannot be: a service acts for exactly one
    # user, so the token is spent once in ``main`` when the service is built and
    # there is no longer an argument here through which a caller could name
    # somebody else. See ``WalletService.open_wallet``.
    wallet = service.open_wallet(Currency[args.currency])
    print(
        f"opened wallet {wallet.wallet_id} "
        f"(currency {wallet.currency.name})"
    )
    return 0


def _whoami(args, actor: User) -> int:
    """Print the identity this invocation acts as."""
    print(f"email: {actor.email}")
    print(f"user_id: {actor.user_id}")
    return 0


def _balance(service: WalletService, args) -> int:
    wallet: Wallet = service.get_wallet(args.wallet_id)
    print(f"status: {wallet.status.name.lower()}")
    print(f"available: {wallet.available_balance}")
    print(f"locked: {wallet.locked_balance}")
    # The pots come last, under the total they add up to, and the order is the
    # point rather than the layout. ``locked`` is *defined* as the sum of these
    # lines, so showing the breakdown underneath is what makes the two readings
    # check out in front of the user - a wallet whose pots do not add up to its
    # locked total would be visible here rather than a mystery later.
    #
    # No line at all when there are no pots: a wallet that never locked anything
    # is the ordinary case, and an empty "funds:" heading would be noise. Note
    # that a wallet with a locked balance *always* has pots now, because the
    # locked balance is nothing but their sum.
    if wallet.funds:
        print("funds:")
        for fund in wallet.funds:
            print(f"  {fund}")
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
        args.wallet_id,
        amount,
        internal_reference,
        destination,
        # The moment this command is running, read here at the adapter rather
        # than inside the use case. It is not decoration: a payout may only spend
        # pots that have come due, so the use case needs to know when "now" is -
        # and being told makes it answerable about a moment other than this one.
        datetime.now(),
        # The pot, if one was named. Omitted, this is the pooled draw - and the
        # omission is honest rather than lazy: a human who does not know which
        # pot they mean has not committed anything, and the wallet spends the
        # matured pots oldest first, exactly as it always has.
        #
        # Note what is *not* passed alongside it: nothing here says when a
        # commitment to pay was made. This is a hand-typed payment, so it has
        # none, and a sealed business pot will refuse it on that ground alone.
        args.fund,
    )

    current = service.get_wallet(args.wallet_id)
    print(
        f"paid {amount} to {destination} | "
        f"available {current.available_balance} | "
        f"locked {current.locked_balance}"
    )
    _deliver_after(factory, settings, deferred_reason)
    return 0


def _fund_command(args, service, factory, settings, deferred_reason) -> int:
    """Route a ``fund`` sub-command to its handler.

    The money-moving verbs are a table lookup and the rest are named explicitly,
    which mirrors how they differ: ``deposit``, ``lock`` and ``release`` share one
    shape and one handler, while opening a pot, extending one and listing them
    each do something no other does and move nothing at all.
    """
    if args.fund_command in _FUND_MONEY:
        return _fund_money(service, args, factory, settings, deferred_reason)
    if args.fund_command == "open":
        return _fund_open(service, args)
    if args.fund_command == "extend":
        return _fund_extend(service, args)
    return _fund_list(service, args)


def _fund_money(service, args, factory, settings, deferred_reason) -> int:
    """Run ``fund deposit``, ``fund lock`` or ``fund release``.

    Three verbs, one shape, so they share a handler - and since this phase, one
    argument list too: all three take the moment they run at. The moment is read
    *here*, at the edge, and passed down. Reading it inside the service would
    mean the same use case could not be asked what it would do at a given time -
    which is exactly what a test of a maturity date needs to do without freezing
    a clock, and now also what a test of a funding moment needs.
    """
    method_name, verb = _FUND_MONEY[args.fund_command]

    # The wallet is loaded for the same reason the top-level commands load it:
    # the amount on this command line carries no currency of its own, and only
    # the wallet can say how to read it.
    wallet = service.get_wallet(args.wallet_id)
    amount = Money(args.amount, wallet.currency)
    internal_reference = args.ref if args.ref is not None else str(uuid.uuid4())

    method = getattr(service, method_name)
    method(
        args.wallet_id,
        args.fund_name,
        amount,
        internal_reference,
        datetime.now(),
    )

    _report_fund(service, args.wallet_id, args.fund_name, f"{verb} {amount}")
    # Only one of these three queues anything - a deposit from outside is the
    # boundary event, while locking and releasing only reshuffle the wallet's own
    # money. Draining is unconditional anyway, for the reason the top-level
    # commands drain unconditionally: a receipt left owed by an earlier failure
    # gets its chance here, and an empty queue costs nothing.
    _deliver_after(factory, settings, deferred_reason)
    return 0


def _fund_open(service, args) -> int:
    """Open a pot.

    Note what is *not* here: no ``_deliver_after``, and no receipt. Opening a pot
    moves no money and says nothing to the owner - see
    ``WalletService.ANNOUNCED`` - so there is nothing queued to drain, and a
    delivery pass would only be a slower way of doing nothing.
    """
    fund = service.open_fund(args.wallet, args.name, FundKind(args.kind), args.matures)
    print(f"opened {fund}")
    return 0


def _fund_extend(service, args) -> int:
    """Push a pot's maturity date later.

    Also silent, for the same reason as ``_fund_open``. The refusals - a date in
    the past, or one that is not later than the current date - come from the
    aggregate and reach the user through ``main``'s ``MoneyError`` handler.
    """
    fund = service.extend_fund(
        args.wallet_id, args.fund_name, args.to, datetime.now()
    )
    print(f"extended {fund}")
    return 0


def _fund_list(service, args) -> int:
    funds = service.funds_for_wallet(args.wallet_id)
    if not funds:
        print(f"no funds on wallet {args.wallet_id}")
        return 0
    for fund in funds:
        print(fund)
    return 0


def _report_fund(service, wallet_id, fund_name, headline) -> None:
    """Report a pot after a change to it, then the wallet's two totals.

    The pot's own balance comes first because the pot is what the command was
    about - naming a pot is the whole point, so seeing *that* pot move is the
    answer. The wallet's totals follow because a pot is not a separate account:
    money moved into one is still the wallet's, and a line showing only the pot
    would leave a user reasonably wondering where the rest went.
    """
    current = service.get_wallet(wallet_id)
    fund = current.fund_by_name(fund_name)
    print(
        f"{headline} | "
        f"{fund.name} {fund.balance} | "
        f"available {current.available_balance} | "
        f"locked {current.locked_balance}"
    )


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
    args, factory, wallet_service: WalletService, settings, deferred_reason, actor
) -> int:
    """Route a ``plan`` sub-command to its handler.

    **``tick`` is not routed here**, and its absence is deliberate rather than an
    oversight: it is the one ``plan`` verb that belongs to the installation
    rather than to a person, so it is dispatched in ``main`` *before* an actor is
    resolved - because resolving one is exactly what it must not need. A tick
    that arrived here would already have required a session, and a scheduler that
    requires a session is a scheduler that needs a privileged account to run
    under. See ``_plan_tick_command``.

    ``settings`` and ``deferred_reason`` arrive from ``main`` rather than being
    read here. They used to be read in this branch, and they moved up with the
    receipts: a wallet command now delivers too, so there are two places that
    need to know how this invocation is configured, and two readers would mean a
    process could in principle act on two different configurations. It also keeps
    the promise ``settings`` makes - that ``os.environ`` is read in one
    module, by the CLI at one moment.

    ``actor`` arrives the same way and for the same reason. ``build_plan_service``
    takes it, because plans are owned and a service that could read another
    user's plan would be the leak this phase closed. Nothing reachable from here
    is built without one.
    """
    plan_service = build_plan_service(
        unit_of_work_factory=factory, actor=actor.user_id
    )
    if args.plan_command == "create":
        return _plan_create(args, plan_service, wallet_service)
    if args.plan_command == "list":
        return _plan_list(args, plan_service)
    if args.plan_command == "show":
        return _plan_show(args, plan_service, wallet_service)
    if args.plan_command == "edit":
        return _plan_edit(args, plan_service)
    return _plan_steer(args, plan_service)


def _plan_tick_command(args, factory, settings, deferred_reason) -> int:
    """Wire up and run one scheduler tick, with no actor anywhere in the call.

    **This is the only command in the CLI that runs as nobody**, and it is
    reachable without a session on purpose. A tick serves every user with a plan,
    so the question "who is acting?" has no answer at this level - and rather
    than invent one, the builders below are the two that take no actor at all.
    ``build_scheduler`` answers it per plan, with the owner each plan carries;
    ``build_notifier`` never reads a wallet, so it has no owner to need.

    All four builders are fed the *same* factory, and for the scheduler that is a
    correctness requirement rather than tidiness: the executor moves money
    through the wallet repositories and records the run through the plan
    repositories, and it relies on both landing in one transaction. A different
    factory would be a different database.

    It sits beside ``_plan_command`` rather than inside it so that the split
    above is visible in the file layout: this function's whole argument list is
    missing the one parameter every other command handler takes, and that is the
    clearest way to say what it is.
    """
    return _plan_tick(
        args,
        build_scheduler(unit_of_work_factory=factory, settings=settings),
        build_notifier(unit_of_work_factory=factory, settings=settings),
        build_deliverer(unit_of_work_factory=factory, settings=settings),
        build_notification_deliverer(unit_of_work_factory=factory, settings=settings),
        deferred_reason=deferred_reason,
    )


def _plan_create(
    args, service: PlanService, wallet_service: WalletService
) -> int:
    # The wallet supplies the currency every bare number on this command is read
    # in, and loading it here has a useful side effect: an unknown wallet fails
    # before anything is built. Its *balance* is deliberately not consulted. A
    # plan may exist before it is affordable - that is what saving towards one
    # means - and the shortfall is reported when a run actually fires.
    #
    # The pot is *not* resolved here even though this method holds the wallet
    # that could do it. It travels as the name the user typed, and
    # ``create_plan`` turns it into an id - because the same method also holds
    # the rule that a locked plan must name a pot and an available one must not,
    # and resolving here would mean checking that rule before the lookup, or
    # reporting "no such pot" for a pot the plan was never going to use.
    wallet = wallet_service.get_wallet(args.wallet)
    plan = service.create_plan(
        wallet_id=args.wallet,
        name=args.name,
        source=PlanSource(args.source),
        schedule=Schedule(cadence=Cadence(args.every), anchor=args.start),
        instructions=_lines(args, wallet.currency),
        ends_on=_end_date(args),
        fund_name=args.from_fund,
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


def _plan_show(args, service: PlanService, wallet_service: WalletService) -> int:
    plan = service.get_plan(args.plan_id)
    print(f"name: {plan.name}")
    print(f"id: {plan.plan_id}")
    print(f"status: {plan.status.value}")
    print(f"source: {plan.source.value}")
    # The pot, resolved to a *name* - and this is the one place a plan's pot can
    # be read as one. The aggregate holds a ``fund_id`` on purpose (see
    # ``SavingsPlan.fund_id``), and a UUID is not an answer to "which pot is my
    # rent paid from?". The name lives on the wallet, so this command loads it.
    #
    # "(pooled)" is printed rather than a blank for a plan with no pot, because
    # the two are different answers and only one of them is a fact about the
    # plan. A plan saved before pots could be named really does spend the
    # matured pots oldest first, and saying so is the honest description - a
    # missing line would read as a rendering bug, and "(none)" would suggest the
    # plan has no source of money at all.
    print(f"pot: {_plan_pot(plan, wallet_service)}")
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


def _plan_pot(plan: SavingsPlan, wallet_service: WalletService) -> str:
    """The pot a plan draws on, as a name. ``"(pooled)"`` when it names none.

    A free function rather than an inline lookup in ``_plan_show`` so that the
    two-line translation - id to name, or no id to "(pooled)" - has one home.

    The wallet is only loaded when there is a pot to look up. A legacy plan
    naming none is the case where an extra read would buy nothing, and loading a
    wallet to print a constant is the kind of cost that is easy to accept and
    never worth accepting.
    """
    if plan.fund_id is None:
        return "(pooled)"
    wallet = wallet_service.get_wallet(plan.wallet_id)
    return wallet.fund_by_id(plan.fund_id).name


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


def _describe(exc: BaseException) -> str:
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
    try:
        # --- the commands that need nobody ----------------------------------
        #
        # Dispatched before any actor is resolved, and that ordering is the
        # design rather than an optimisation. The three identity commands cannot
        # need a session - they are how a session comes to exist, or cease to -
        # and ``plan tick`` must not, because it serves the whole installation
        # and there is no person it could act as. Resolving an actor first, as
        # this function used to do unconditionally, would have made a scheduler
        # that requires a login.
        if args.command == "signup":
            return _signup(args, factory)
        if args.command == "login":
            return _login(args, factory)
        if args.command == "logout":
            return _logout(args, factory)
        if args.command == "plan" and args.plan_command == "tick":
            return _plan_tick_command(args, factory, settings, deferred_reason)

        # --- everything else runs as somebody, proved by the stored token ----
        #
        # Once, before any service is built, because every one of them is
        # constructed *for* this user - the same arrangement the HTTP side has,
        # where one resolved actor is handed to the services built per request.
        # A token that is unknown, expired or orphaned refuses the command here,
        # before anything is read or moved, which is what makes "not signed in"
        # and "signed in as somebody else" impossible to confuse.
        actor = _current_actor(args, factory)
        service = build_wallet_service(
            unit_of_work_factory=factory, settings=settings, actor=actor.user_id
        )
        if args.command == "whoami":
            return _whoami(args, actor)
        if args.command == "plan":
            return _plan_command(
                args, factory, service, settings, deferred_reason, actor
            )
        if args.command == "fund":
            return _fund_command(args, service, factory, settings, deferred_reason)
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
    except (MoneyError, CliError) as exc:
        # Two roots, one renderer. A domain refusal and a refusal this
        # presentation makes arrive at the same line of output and the same exit
        # code, because from a terminal they are the same event: the command did
        # not do the thing, and here is why. The API makes the identical
        # arrangement in ``errors.install``, where ``MoneyError`` and ``ApiError``
        # are registered side by side.
        print(f"error: {_describe(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
