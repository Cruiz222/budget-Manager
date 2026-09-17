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

from app.application.identity.log_in import LoggedIn
from app.application.plan_service import PlanService
from app.application.profile_service import ProfileStanding
from app.application.wallet_service import WalletService
from app.composition_root import (
    build_confirm_email_change,
    build_confirm_password_reset,
    build_confirm_phone_sign_up,
    build_deliverer,
    build_log_in,
    build_log_out,
    build_notification_deliverer,
    build_notifier,
    build_plan_service,
    build_profile_service,
    build_reconciler,
    build_request_email_change,
    build_request_password_reset,
    build_request_phone_verification,
    build_resolve_actor,
    build_scheduler,
    build_sign_up,
    build_wallet_service,
)
from app.domain.identity.profile import Profile
from app.domain.identity.tier import Tier, limits_for
from app.domain.identity.user import User
from app.domain.money.confirmationKind import ConfirmationKind
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
    paystack_from_environment,
    session_path as configured_session_path,
    termii_from_environment,
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

#: Which of the commands above are answered rather than merely run.
#:
#: **One name, and the sparseness is the decision.** ``withdraw`` moves money out
#: of the wallet for good - it crosses the system's edge and cannot be called
#: back - so it takes a second look before it happens. ``deposit`` does not: it
#: brings money in, a mistyped deposit is corrected by withdrawing, and a prompt
#: in front of it would be a prompt people learn to answer without reading. The
#: CLI has no ``close`` verb, so the third confirmed operation over HTTP has no
#: row here at all.
#:
#: This is a set rather than a flag on ``OPERATIONS`` because the two tables
#: answer different questions - one is "how do I report this verb", the other is
#: "does this verb need a person" - and a flag would have merged them into a
#: shape where the second question could only be asked by reading the first.
#: ``_operation`` dispatches on it, and ``add_commands`` reads it to decide which
#: parsers get ``--yes``.
_CONFIRMED_OPERATIONS = {"withdraw"}

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


def _prompt_code(label: str) -> str:
    """Ask for a code that was mailed, invisibly, and return it stripped.

    ``getpass`` for ``_prompt_password``'s reason, and the argument is stronger
    here rather than weaker. A password is a secret its owner chose and can change;
    one of these is a 256-bit value that moves an account's address or replaces its
    password, it is valid for fifteen minutes, and it is the *whole* of the
    authorisation for the operation it belongs to. ``--token abc123`` would put it
    in the shell's history file, in ``ps`` output for the duration, and in whatever
    the terminal is recording - and somebody who reads it out of any of those within
    the window can take the account, with no password and no session.

    Read invisibly rather than echoed, too, and that is not only about
    over-the-shoulder reading: a terminal that echoes it has written it to
    scrollback, which outlives the fifteen minutes.

    There is no confirmation prompt, unlike ``signup``. A mistyped password at
    sign-up creates an account nobody can open, which is why that one asks twice;
    a mistyped code simply does not match anything and is refused with the refusal
    that says so, and the person is holding the mail it came from.

    **The label is a parameter because two commands prompt for two different
    codes**, and the difference is not cosmetic: "confirmation code" belongs to an
    address change and "reset code" to a password reset, and a person who has asked
    for one and is looking at a prompt naming the other would reasonably wonder
    whether they were in the right command. Two constants at the call sites rather
    than two functions, because everything below the label is identical.

    **The value is stripped, and that is the fix decision 180 recorded and did not
    ship.** ``getpass`` returns exactly what was typed, so a code selected with a
    double-click and pasted arrives with the trailing whitespace the terminal
    copied - and one of these codes is base64url, which makes ``+`` and ``/``
    plausible-looking characters that a person may well paste with a trailing
    space or a newline. Before this, that refusal read "the code means nothing",
    which is true, unhelpful, and identical to the refusal for a code that genuinely
    means nothing. Stripping is safe because the alphabet a token is drawn from
    contains no whitespace at all, so a stripped code is either the code or a
    different string that was never going to match.

    What is deliberately *not* stripped is a password, in ``_prompt_password``
    above: ``PlainPassword``'s docstring makes that argument in full, and it turns
    on a password being a secret the person chose rather than a value this system
    minted. A code has no such property - every character in it is ours.
    """
    return getpass.getpass(label).strip()


def _confirmed(question: str, assume_yes: bool) -> bool:
    """Ask a yes/no question at the terminal. Returns whether to go ahead.

    ``input`` and not ``getpass``, which is the opposite choice to
    ``_prompt_password`` above and for the opposite reason: a password is a
    secret and must not be seen, while this is a question whose whole value is
    that the person reads it. Hiding it would defeat it.

    **An unanswered prompt is not a yes.** ``EOFError`` - an unattended run with
    nothing on stdin, which is what a cron job or a piped invocation gives - is
    caught and read as a refusal, as is an empty line. Neither is an error and
    neither is agreement: the only ways past this are typing ``y`` or passing
    ``--yes``, and ``--yes`` is a person saying "there is nobody here to ask".
    Defaulting the other way would make the prompt a decoration on any machine
    whose stdin happens to be closed.

    ``OSError`` is caught alongside it, and that is not defensive padding - it is
    the same situation arriving by a different route. A closed file descriptor
    raises ``OSError`` from ``input`` where an exhausted pipe raises ``EOFError``,
    and the two are one fact from this function's point of view: nobody is there
    to ask. It was found by running the suite rather than by reasoning about it -
    pytest replaces stdin with an object whose ``readline`` raises ``OSError``,
    so the first CLI test to reach this prompt without ``--yes`` failed with a
    traceback instead of with the refusal the docstring above promises.

    **Only ``y`` is accepted, and it is matched case-insensitively.** Not "any
    answer that is not 'n'" - the cost of the two mistakes is asymmetric. A
    mistyped ``y`` costs a second run of the command; a mistyped ``n`` that was
    read as agreement costs money that cannot be called back. Everything that is
    not a clear yes is a no.

    **Refusing is not an error.** The caller returns 0, because nothing failed:
    a person declined, and the request is left in the database to expire on its
    own. A non-zero exit here would tell a script that something went wrong when
    the system did exactly what it was told.
    """
    if assume_yes:
        return True
    try:
        answer = input(f"{question} [y/N] ")
    except (EOFError, OSError):
        # Nothing to read. Printed so the user knows why nothing happened rather
        # than seeing a command that silently did nothing at all.
        print("no answer given - nothing was moved")
        return False
    if answer.strip().lower() != "y":
        print("declined - nothing was moved")
        return False
    return True


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


def _optional_date(value: str) -> date | None:
    """A date, or nothing at all - for a flag whose value can also be cleared.

    ``set-profile``'s ``--birth-date`` and the only reason this wrapper exists.
    ``_date("")`` is an argparse refusal, so a command typed with an empty flag
    would exit 2 with a usage line rather than reaching the handler - which would
    leave a birth date typed in wrong with no way to remove it, while every text
    field beside it could be cleared by passing nothing. Empty means absent, the
    same way it does for those fields and for the same reason.

    An empty *default* is not affected: ``--birth-date`` defaults to
    ``_NOT_PASSED``, which never reaches a parser, so this function only ever
    sees a value somebody actually typed.
    """
    if not value.strip():
        return None
    return _date(value)


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


class _NotPassed:
    """The absence of a flag, as a value - which is not the same as an empty one.

    Only ``set-profile`` uses this, and it exists because that command *edits* a
    record whose fields each have three possible requests against them: leave it
    alone, clear it, or set it. ``None`` already means the middle one everywhere
    else in this codebase, so a field left off the command line cannot also
    arrive as ``None`` without the command losing the ability to tell the two
    apart - and the cost of getting it wrong is that
    ``set-profile --display-name Ada`` silently drops a complete profile's tier
    by clearing the six fields it was not asked about.

    A class rather than ``object()`` so that a debugging session printing a
    parsed namespace shows ``<not passed>`` instead of a memory address, and so
    that ``is`` comparisons in tests can name what they are looking for.
    """

    def __repr__(self) -> str:
        return "<not passed>"


#: The default for every ``set-profile`` flag. See ``_NotPassed``.
#:
#: Deliberately *not* used as a *value* for a flag that was passed: argparse
#: ``type=`` callables never see it, because a default is used as-is when the
#: flag is absent. So the only way to arrive at the handler holding this object
#: is for the user not to have typed the flag at all.
_NOT_PASSED = _NotPassed()


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

    # --- identity: the commands that need no session -------------------------
    #
    # They are top-level rather than nested under a noun, because there is no
    # noun that covers them - ``account signup`` would suggest the others act
    # on an account too, and logging out discards a token rather than touching
    # one. And they are *eight* rather than three now, which is what an address
    # change of its own, then a password reset, then a phone signup added to this
    # file: ``confirm-email`` and ``confirm-password-reset`` are each authorised by
    # a code mailed out, so neither needs a session and both belong here rather
    # than beside the command that asks for them - ``reset-password`` is here
    # because the person running it is locked out and has no session to offer, and
    # ``signup-phone``/``confirm-phone`` are here because the account is what
    # answering the texted code creates, so there is nothing yet to log in as.
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

    # ``signup-phone`` sits beside ``signup`` rather than beside ``login``, because
    # the two are the same act through two identifiers - and the account it creates
    # is the subject of both. ``confirm-phone`` is the second half of *this* flow
    # and is declared next to it rather than beside ``confirm-email``, where it
    # would belong by the shape of its authorisation: reading the pair together is
    # worth more than grouping by "which commands take a code".
    phone_parser = subparsers.add_parser(
        "signup-phone",
        help="ask for a code that will let this number create an account",
    )
    phone_parser.add_argument(
        "phone",
        help="the number to prove - any spelling, since it is folded before use",
    )
    # No --password: the password is chosen at the confirm, by whoever read the
    # text. Nothing is taken from argv here at all.

    confirm_phone_parser = subparsers.add_parser(
        "confirm-phone",
        help="create the account, using the code that was texted",
    )
    # No --code and no --password, and both absences are deliberate - the pair
    # ``confirm-password-reset`` documents. The code is a credential that creates
    # an account and expires in ten minutes; the password is a secret that must
    # never be an argument. Nothing is taken from argv by this command at all.

    login_parser = subparsers.add_parser(
        "login", help="obtain a session token and store it at --session"
    )
    login_parser.add_argument("email", help="the address to sign in as")

    # ``login-phone`` sits beside ``login`` rather than replacing it with an
    # argument that could be either, and the reason is the one ``LogInIn`` gives
    # at the other boundary: an identifier that arrives as one string has to be
    # classified, and the only available test is a guess about its shape. Here the
    # command name is the statement, so a person typing the wrong one is typing
    # the wrong *command* - and the two are listed together by ``--help``, which
    # is where that mistake gets caught.
    login_phone_parser = subparsers.add_parser(
        "login-phone",
        help="obtain a session token for a number, and store it at --session",
    )
    login_phone_parser.add_argument(
        "phone",
        help="the number to sign in as - any spelling, since it is folded before use",
    )
    # No --password, for the reason every other command here omits it: an argument
    # lands in the history file. See ``_prompt_password``.

    subparsers.add_parser(
        "logout", help="discard the stored token and end the session"
    )

    # ``confirm-email`` belongs with the three above and not with the commands
    # that act as somebody, which is why it is declared here rather than beside
    # ``change-email`` below. It reads no session file at all: the code is the
    # whole of the authorisation, and requiring a login to answer a mail would
    # refuse exactly the person who asked on this machine and opened the mail on
    # another. See ``_confirm_email``.
    confirm_parser = subparsers.add_parser(
        "confirm-email",
        help="apply a requested address change, using the code that was mailed",
    )
    # No --token argument, and its absence is deliberate. See ``_prompt_code``:
    # this one moves an account and expires in fifteen minutes, so the history
    # file is the last place it should be.

    # ``reset-password`` and ``confirm-password-reset`` are the fifth and sixth
    # members of this group, and they are the two commands a person runs when they
    # cannot log in at all - which is why they are here and not below the actor
    # line. ``reset-password`` is the only command in this file that runs as
    # nobody *and* has nothing to prove: the caller is a stranger by construction,
    # and the address they type is the whole of the input. ``confirm-password-reset``
    # is the fourth command authorised by a mailed code, and the first whose code
    # replaces a secret rather than moving an account.
    reset_parser = subparsers.add_parser(
        "reset-password",
        help="ask for a code that lets this account set a new password",
    )
    reset_parser.add_argument("email", help="the address of the account to reset")

    confirm_reset_parser = subparsers.add_parser(
        "confirm-password-reset",
        help="set a new password, using the code that was mailed",
    )
    # No --token and no --password, and both absences are deliberate. See
    # ``_prompt_code`` and ``_prompt_password``: the first replaces an account's
    # password and expires in fifteen minutes, the second is a secret that must
    # never be an argument. Nothing is taken from argv by this command at all.

    change_parser = subparsers.add_parser(
        "change-email",
        help="ask to move this account's address to a new one",
    )
    change_parser.add_argument("email", help="the address to move to")
    # No --password argument either, for ``signup``'s reason. The session is
    # required as well, and the pair is the design: the token says which account,
    # and the password says that whoever holds it still knows the secret.

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

    # ``profile`` and ``set-profile`` are the two commands about who the account
    # holder *is*, as opposed to which account they hold - and they sit beside
    # ``whoami`` rather than near ``balance`` because that is the pair they are
    # the other half of. ``whoami`` answers "which account is this", which is
    # security-shaped; these answer "who is this person", which is what a tier is
    # derived from. Neither touches money and neither takes a wallet.
    #
    # Two commands rather than one with a ``--show`` flag: reading a profile and
    # replacing one are different enough to be different verbs, and the read is
    # the one a person runs to find out what this system thinks they are - which
    # includes the ceilings they are under, and which they should be able to ask
    # for without passing anything at all.
    subparsers.add_parser(
        "profile",
        help="show your own details, your tier and the limits it imposes",
    )

    set_profile_parser = subparsers.add_parser(
        "set-profile",
        help="give your details, which is what raises your tier",
    )
    # Every flag defaults to ``_NOT_PASSED``, which is the whole reason that
    # sentinel exists. The default cannot be ``None``, because ``None`` already
    # means something here - "this field is not given", which for an *edit* is a
    # request to clear it. With a ``None`` default the two requests "leave my
    # phone number alone" and "delete my phone number" would arrive as the same
    # value, and the command would have to pick one; it would pick wrongly for
    # half its callers, and the wrong choice for a *complete* profile is that
    # omitting a flag silently drops the person's tier. Three states, three
    # spellings: not passed at all (the sentinel), passed empty (clear it), and
    # passed a value (set it).
    #
    # REQUIRED only when there is nothing to edit yet, which is why it has no
    # ``required=True`` here: ``--display-name`` is the one field a profile must
    # have and the one field an existing profile cannot lose, so it is needed
    # exactly on the first invocation. argparse cannot express "required
    # conditionally", so the handler checks and says which case it is in. See
    # ``_set_profile``.
    #
    # No ``--tier`` flag, and its absence is the feature rather than an omission.
    # A tier is derived from which of these fields are present and is stored
    # nowhere, so there is no value to set and no command that could set one -
    # which is what makes "complete your profile" the only way up. A ``--tier``
    # here would be a client-supplied tier, and the API does not accept one
    # either.
    set_profile_parser.add_argument(
        "--display-name",
        default=_NOT_PASSED,
        help="the name to show; required the first time, since a profile "
        "with nothing in it is a row that exists to say nothing",
    )
    set_profile_parser.add_argument(
        "--first-name",
        default=_NOT_PASSED,
        help="your legal first name, as an identity check would read it; "
        "pass an empty string to clear it",
    )
    set_profile_parser.add_argument(
        "--last-name",
        default=_NOT_PASSED,
        help="your legal surname; pass an empty string to clear it",
    )
    # ``_optional_date`` rather than ``_date``: this is the one flag whose parser
    # would otherwise swallow the empty string, and a birth date typed wrong is
    # the field most likely to need deleting.
    set_profile_parser.add_argument(
        "--birth-date",
        default=_NOT_PASSED,
        type=_optional_date,
        help="your date of birth as YYYY-MM-DD; a date, not a moment. "
        "Pass an empty string to clear it",
    )
    set_profile_parser.add_argument(
        "--phone",
        default=_NOT_PASSED,
        help="a number that reaches you, e.g. +2348000000000; pass an empty "
        "string to clear it",
    )
    set_profile_parser.add_argument(
        "--country",
        default=_NOT_PASSED,
        help="a two-letter country code, e.g. NG; pass an empty string to "
        "clear it",
    )
    set_profile_parser.add_argument(
        "--address",
        default=_NOT_PASSED,
        help="one line of your address; pass an empty string to clear it",
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
    payout_parser.add_argument(
        "--yes",
        action="store_true",
        help="do not prompt; for scripts and unattended runs",
    )

    for name, _ in OPERATIONS.items():
        op_parser = subparsers.add_parser(name, help=f"{name} money")
        op_parser.add_argument("wallet_id", type=_uuid)
        op_parser.add_argument("amount", type=_decimal)
        op_parser.add_argument(
            "--ref",
            help="idempotency key (auto-generated if omitted)",
        )
        if name in _CONFIRMED_OPERATIONS:
            op_parser.add_argument(
                "--yes",
                action="store_true",
                help="do not prompt; for scripts and unattended runs",
            )

    _add_plan_commands(subparsers)
    _add_fund_commands(subparsers)
    _add_reconcile_command(subparsers)

    return parser


def _add_reconcile_command(subparsers) -> None:
    """The ``reconcile`` verb - a top-level command, unlike ``plan tick``.

    A top-level verb rather than a ``plan`` sub-command, and the placement is
    the argument: reconciliation has nothing to do with plans. It asks a payment
    provider about deposits whose webhook never arrived, so filing it under
    ``plan`` would put it behind a noun that does not describe it - and a reader
    looking for "what asks Paystack what happened" would not think to look there.
    ``plan tick`` is nested because a tick *is* about plans; this is not.

    It takes ``--as-of`` for ``plan tick``'s reason, and with the same parse: the
    grace window is judged against a moment, and a command that could only judge
    it against the wall clock would be a command whose most interesting behaviour
    - "these rows are too young to ask about" - could not be exercised without
    waiting for it.
    """
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="ask the payment provider about deposits still in flight "
        "(the reconciliation entry point)",
    )
    reconcile_parser.add_argument(
        "--as-of",
        type=_datetime,
        default=datetime.combine(date.today(), time.min),
        help="the moment to treat as now, e.g. 2026-03-02T12:00 "
        "(default: today at midnight)",
    )


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


def _signup_phone(args, factory, termii) -> int:
    """Ask for a code that will let a number create an account.

    **This reads no session file and proves nothing at all** - the second command
    in this file of which both are true, after ``reset-password`` - and it is the
    one that goes one step further out. ``reset-password`` is run by somebody who
    cannot authenticate; this is run by somebody who may not *be* anybody in this
    system, because the account is what answering the code creates. Placing it above
    the actor line is what makes it work on a machine nobody has ever logged in on,
    which is the only kind of machine a signup has ever been run on.

    **The number is not echoed back as it was typed, and the difference is worth
    knowing before it looks like a bug.** ``PhoneVerification.issue`` folds whatever
    arrives, so ``0801 234 5678`` and ``+2348012345678`` are one number and the row
    holds one spelling - the one the provider will be handed. Printing the folded
    value is therefore the truth about which handset the code goes to, and it is the
    value the person will need to type at the confirm if they want to check
    anything. ``_reset_password`` prints ``args.email`` instead, which is the same
    choice made the other way, and the reason it differs is that an address has no
    fold a user would not recognise.

    **The no-SMS refusal is not handled here**, following ``_reset_password`` exactly:
    ``build_request_phone_verification`` composes the reason from the environment -
    naming ``TERMII_API_KEY`` or ``TERMII_SENDER_ID`` - and the use case raises it,
    which ``main``'s ``except (MoneyError, CliError)`` renders as ``error: ...`` and
    exit 1. The same path every other refusal takes, so there is nothing to write
    here for it.

    A **failed send propagates**, and the exit code is 1 for the reason the API
    documents: the row is already committed and holds a hash of a code nobody has,
    so reporting success would leave the person waiting on a handset for a text that
    is not coming.
    """
    verification = build_request_phone_verification(
        unit_of_work_factory=factory, settings=termii
    ).execute(args.phone, datetime.now())

    print(
        f"verification code sent to {verification.phone} | "
        f"expires {_moment(verification.expires_at)}"
    )
    print("run 'confirm-phone' and enter the code to create the account")
    return 0


def _confirm_phone(args, factory) -> int:
    """Create the account, using the code that was texted, and hold that number.

    **It reads no session file, and the argument is ``confirm-email``'s** - the code
    was texted to the number being claimed, so presenting it proves something no
    session could, and there is no session to require anyway: the account does not
    exist until this command succeeds. It is the only command in this file that
    creates something from a credential a *third party* delivered.

    **The password is asked for twice**, exactly as ``confirm-password-reset`` asks
    for its own and with the same argument, which is worth restating because the
    stakes here are the mirror image: a mistyped password is accepted, stored, and
    becomes the secret on a brand-new account, which is a lockout created at the
    moment of creation. That person has no address on the account, so the way back
    in is a text rather than a mailbox - and until the reset-by-SMS slice lands,
    that way back is designed and not yet reachable, which makes a mistyped
    password here the worse of the two versions of the same mistake.

    **The policy is not checked here.** ``PlainPassword`` owns it, and a weak
    password is refused by the use case *before* the code is spent, so a person who
    types six characters can present the same code again rather than asking for
    another text.

    **What it prints next changed when ``login-phone`` arrived, and the history is
    worth a sentence.** Until that command existed this printed no "next" line at
    all, because ``signup``'s - "run 'login'" - would have sent the operator to a
    command that resolves an address, and the account created here has none. The
    line is back in its own form now that there is a command that works, and the
    warning it used to carry stays because it is still true: the account holds no
    address, and a deposit from it is refused until one is set. That address is
    reachable in principle - ``request_email_change`` treats a ``None`` as "set
    one" rather than "move one" - and not yet from this command, whose subject is
    always an actor with a session. What is printed is the number, so that whoever
    needs it can find it in the output afterwards.
    """
    user = build_confirm_phone_sign_up(unit_of_work_factory=factory).execute(
        _prompt_code("verification code: "),
        _prompt_password(confirm=True),
        datetime.now(),
    )
    print(f"registered {user.phone} ({user.user_id})")
    print("no address is on this account yet, so deposits are refused until one is set")
    print("next: run 'login-phone' to start a session")
    return 0


def _login(args, factory) -> int:
    """Obtain a token for an address and put it where every later command looks.

    **The commands that write the session file are this one and ``login-phone``**,
    which is worth knowing when something goes wrong with the CLI's identity:
    there is exactly one place a token can come from, and both of them reach it
    through ``_start_session`` below. It is a password typed at a prompt, and
    which of the two commands asked for it changes only which lookup ran.

    Overwriting an existing session is not refused. Logging in as somebody else
    while already signed in is a normal thing to want, and the alternative - a
    ``logout`` first - would mean two commands to do one thing, with a state in
    between where the machine is signed in as nobody. The old session is left
    alone on the server, which is the same shape ``LogIn`` describes for two
    simultaneous logins: it expires on its own, and nothing here decides that a
    person only has one device.
    """
    return _start_session(
        args,
        build_log_in(unit_of_work_factory=factory).execute(
            args.email, _prompt_password(confirm=False), datetime.now()
        ),
    )


def _login_phone(args, factory) -> int:
    """Obtain a token for a number, and put it where every later command looks.

    ``_login``'s sibling, and the command that closes the gap ``_confirm_phone``
    used to print a warning about: an account created from a texted code has no
    address, so until this existed there was no way to start a session as one, and
    the account could hold a wallet it could not reach.

    **It is a separate command rather than an argument to ``login``**, for the
    reason the parser gives one screen up: the kind of identifier is stated by
    which command was run, not reconstructed from what the string looks like.

    What it does *not* do is guess. A number that was never registered, a number
    belonging to somebody whose password is different, and an address typed into
    this command instead of the other one all produce the same ``error:`` line and
    the same exit code 1, because all three are ``InvalidCredentialsError`` - see
    ``LogIn``. A person who ran the wrong command finds out from ``--help`` rather
    than from a message that confirmed a number is registered.
    """
    return _start_session(
        args,
        build_log_in(unit_of_work_factory=factory).execute_for_phone(
            args.phone, _prompt_password(confirm=False), datetime.now()
        ),
    )


def _start_session(args, logged_in: LoggedIn) -> int:
    """Write the token to the session file and report what was signed in as.

    **The one place a token reaches the disk**, which ``_login``'s docstring
    claimed for itself for as long as it was the only command that could produce
    one. It is a function now rather than a paragraph because there are two, and
    the guarantee has to survive the second: a session file written somewhere else
    would be a second answer to "who is this machine signed in as", free to
    disagree with the first.

    The token goes in whole and the *session* is not written at all - only the
    expiry is printed, because it is the one fact about a session a person needs
    before it stops working. See ``_write_token`` for the file's shape.
    """
    _write_token(args.session, logged_in.token)
    print(
        f"logged in as {_who(logged_in.user)} | "
        f"expires {_moment(logged_in.session.expires_at)} | "
        f"token stored at {args.session}"
    )
    return 0


def _who(user: User) -> str:
    """The identifier this account signs in by, as a person would say it.

    ``User`` holds an address, a number, or both - never neither - so this always
    names something, and it prefers the address when there are two because that is
    the one a person gave first. It exists because the two login commands print the
    same line and a phone-only account is a case where the obvious
    ``user.email`` renders as ``None``: a signed-up account being reported as
    ``logged in as None`` is the kind of output that gets read as a bug in the
    password check.
    """
    return user.email or user.phone


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


def _confirm_email(args, factory, settings) -> int:
    """Apply a requested address change, using the code that was mailed.

    **This reads no session file, and that is the whole of its design.** The code
    was mailed to the address the account is moving *to*, so presenting it proves
    something a session does not - which is why this sits with ``signup``,
    ``login`` and ``logout`` above rather than below the actor line, and why it
    works on a machine where nobody has ever logged in. Somebody who asks at a
    desk and opens the mail on a phone is the case the whole flow is arranged
    around; requiring a login here would refuse exactly them.

    It is not a privileged command, and the difference is worth stating where the
    missing token check would otherwise look like one: the code names one account
    and moves that one account, and it exists only because somebody already
    presented that account's password to mint it. Nothing here decides *which*
    account to touch - that is read off the row the code names.

    **The output distinguishes the two mails**, because they fail differently and
    only one of them is a problem. The notice to the old address is best-effort,
    so a failure is reported as a note rather than raised; the change itself has
    already happened by the time this prints, and saying so first is what makes the
    note read as a footnote rather than as a failure.
    """
    result = build_confirm_email_change(
        unit_of_work_factory=factory, settings=settings
    ).execute(_prompt_code("confirmation code: "), datetime.now())

    print(f"email changed to {result.user.email} | was {result.previous_email}")
    if result.notice_sent:
        print(f"{result.previous_email} was told about the change")
    elif result.notice_error is not None:
        # Not an error and not an exit code: the account moved, and this is the
        # courtesy that did not land. Printed in full because the reason is a
        # connection or a mailbox rather than anything the person typed.
        print(f"note: {result.previous_email} could not be told: {result.notice_error}")
    else:
        print(
            f"note: this installation has no email configured, so "
            f"{result.previous_email} was not told"
        )
    return 0


def _reset_password(args, factory, settings) -> int:
    """Ask for a code that lets this account set a new password.

    **This reads no session file and proves nothing at all**, and it is the only
    command in this file of which both are true. ``confirm-email`` reads no session
    either, but it presents a code somebody was mailed; this one presents an
    address and nothing else, because the person running it cannot authenticate -
    that is what a forgotten password is. Placing it above the actor line is what
    makes it work on a machine nobody has ever logged in on.

    **What it prints differs from the API on purpose, and this is the one place the
    two presentations genuinely disagree.** A terminal that can write to this
    database is not a public surface - its operator could run ``sqlite3`` and read
    the ``users`` table - so withholding "no account reads mail at that address"
    would protect nobody and strand the person who cannot tell a typo from a
    delivery failure. The API answers identically for both cases because a stranger
    with a list of addresses is a different caller entirely; here, the honest answer
    is the useful one.

    **The no-mail refusal is not handled here.** ``build_request_password_reset``
    composes the reason from the environment and the use case raises it with the
    variable named, which ``main``'s existing ``except (MoneyError, CliError)``
    renders as ``error: ...`` and exit 1 - the same code path every other refusal
    takes, so there is nothing to write here for it.

    A **failed send propagates**, exactly as the API's does, and the exit code is 1
    for the same reason: the person is locked out, and telling them a code is on its
    way when it is not would leave them waiting for a mail that will never come.
    """
    outcome = build_request_password_reset(
        unit_of_work_factory=factory, settings=settings
    ).execute(args.email, datetime.now())

    if not outcome.requested:
        # Not an error: it is the answer to a typo, and the person needs to know
        # which of the two things happened to act on it. See the docstring for why
        # this sentence exists in this presentation and not in the API's.
        print(f"no account reads mail at {args.email} | nothing was sent")
        return 0

    print(
        f"reset code sent to {args.email} | "
        f"expires {_moment(outcome.expires_at)}"
    )
    print("run 'confirm-password-reset' and enter the code to set a new password")
    return 0


def _confirm_password_reset(args, factory, settings) -> int:
    """Set a new password, using the code that was mailed, and sign every device out.

    **It reads no session file, and the argument is ``confirm-email``'s** - the code
    was mailed to the address the account holds, so presenting it proves something a
    session cannot, and requiring a login to answer the mail would refuse exactly the
    person who asked on a laptop and read the mail on a phone. There is a second
    reason here that has no counterpart in the change flow: this command *ends every
    session on the account*, so a person who ran it while signed in is signed out by
    it, and a command that demanded a live session would be deleting the credential
    it had just required.

    **The new password is asked for twice**, where the code is asked for once, and
    the asymmetry is deliberate. A mistyped code is refused by the lookup; a
    mistyped *password* is accepted, stored, and silently replaces the one the
    person thought they were setting - which recreates the lockout this whole
    command exists to end, and does it while reporting success. That is the same
    failure ``signup`` guards against with the same prompt, with more at stake: an
    account created with an unknown password has never been used, and this one has
    an owner who is trying to get back into it.

    **The policy is not checked here.** ``PlainPassword`` owns it, and a weak
    password is refused by the use case *before* the code is spent - so a person who
    types six characters can present the same code again. A check in this file would
    be a second copy of the rule, and it would move the refusal to a place that
    cannot know whether the code was spent.

    **The output reports the password change first**, because the two things after
    it are commentary on something that has already happened. The session count is
    printed rather than kept quiet - a person who was signed in on a phone will find
    it signed out, and that is the fact that makes the revocation visible rather
    than a surprise. A notice that bounced is a footnote and never a failure, for
    the reason the API documents: the password has already changed, and exiting
    non-zero would report a reset that happened as one that did not.
    """
    result = build_confirm_password_reset(
        unit_of_work_factory=factory, settings=settings
    ).execute(
        _prompt_code("reset code: "),
        _prompt_password(confirm=True),
        datetime.now(),
    )

    print(f"password changed for {result.user.email}")
    if result.sessions_revoked:
        print(
            f"{result.sessions_revoked} "
            f"{'session' if result.sessions_revoked == 1 else 'sessions'} signed out"
        )
    else:
        print("no sessions were signed in, so none had to be ended")

    if result.notice_sent:
        print(f"{result.user.email} was told about the change")
    elif result.notice_error is not None:
        print(f"note: {result.user.email} could not be told: {result.notice_error}")
    else:
        print(
            f"note: this installation has no email configured, so "
            f"{result.user.email} was not told"
        )
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


def _change_email(args, factory, settings, actor: User, deferred_reason) -> int:
    """Ask to move this account to a new address, proving the password again.

    Below the actor line, unlike ``confirm-email`` above it, because asking is
    something done *as* an account: the caller is resolved from the stored token
    and the password is checked against that account's credential. Either alone
    would be weaker than the pair - a stolen token is not enough to move an
    account, and a password typed into a terminal that is signed in as somebody
    else is not enough either.

    **What it prints depends on which of two things happened**, and the two are
    different enough that it must not be left to be inferred from a status word.
    On an installation with mail, a code has gone to the new address and the change
    has *not* happened yet - ``confirm-email`` is the next step, and the deadline
    is printed because what the person needs is *when*, not "fifteen minutes". On
    an installation with no mail there is nothing to confirm and the change is
    already applied, and the command says so **and says why**, naming the missing
    variable. That is the plan's rule arriving at a terminal: an install without
    mail must never be *silently* less safe than one with it.
    """
    outcome = build_request_email_change(
        unit_of_work_factory=factory,
        settings=settings,
        actor=actor.user_id,
    ).execute(args.email, _prompt_password(confirm=False), datetime.now())

    if outcome.applied:
        print(f"email changed to {outcome.email} | was {actor.email}")
        # ``deferred_reason`` is the same string the deliverers report, so the
        # explanation of "no email" reads identically wherever it appears.
        if deferred_reason is not None:
            print(
                f"note: the change was applied without a confirmation step - "
                f"no email is configured ({deferred_reason})"
            )
        return 0

    print(
        f"confirmation code sent to {outcome.email} | "
        f"expires {_moment(outcome.expires_at)}"
    )
    print("run 'confirm-email' and enter the code to complete the change")
    return 0


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
    """Print the identity this invocation acts as.

    **An account with no address can now hold a session, which is what makes
    these lines conditional rather than fixed.** ``login-phone`` is the command
    that made that reachable, and ``email: None`` is the output a person would
    read as a bug in the login rather than as the fact it is - there is no address
    on this account, and the number is what identifies it.

    So each line is printed when the account holds that identifier, and the ones
    it does not hold are left out rather than filled in. ``_given``'s "not given"
    is deliberately *not* reused, although it is this file's word for an empty
    profile field: nothing was withheld here by a person filling in a form, and
    the account is fully identified by what is printed. At least one line always
    appears, because ``User`` refuses to exist without an identifier.
    """
    if actor.email is not None:
        print(f"email: {actor.email}")
    if actor.phone is not None:
        print(f"phone: {actor.phone}")
    print(f"user_id: {actor.user_id}")
    return 0


def _given(value) -> str:
    """Render a profile field that may never have been filled in.

    ``str`` rather than a type check, because the seven fields are four shapes -
    a string, a country code, a ``date``, and ``None`` - and all four already
    render the way a person reads them. A ``date`` prints as ISO, which is the
    same spelling the flag that set it takes.
    """
    return "not given" if value is None else str(value)


def _ceiling(limit: Money | None) -> str:
    """Render one ceiling, including the one that is not there.

    ``None`` is a real value in ``TierLimits``: it means this tier has no ceiling
    of this kind, which is a rule rather than a missing number. Printing the word
    ``None`` for it would read as a value somebody forgot to fill in. No tier
    lifts a ceiling today - the branch is here because the type allows it, and a
    tier that did lift one must not render as a bug.
    """
    return "no limit" if limit is None else str(limit)


def _print_standing(standing: ProfileStanding) -> None:
    """Print a profile, the tier it puts somebody at, and every ceiling that implies.

    Shared by ``profile`` and ``set-profile`` rather than written out twice, and
    the sharing is the feature: ``set-profile`` answers "did my tier move, and
    which limits moved with it?" by printing the same block ``profile`` prints,
    so the two commands cannot come to describe a tier two different ways.

    **The ceilings are printed for every currency**, matching ``ProfileOut`` on
    the HTTP side and for the reason argued there: the limits table is one
    published rule keyed by ``(tier, currency)`` and a profile carries no
    currency to narrow it to. The terminal has even less to narrow it *with* -
    no command here lists a person's wallets - so a version of this that guessed
    a currency would print a limit that might not be the one that applies, which
    is worse than printing five.

    **The note is printed only when the tier is not the top one**, and it names
    the four fields rather than saying "your profile is incomplete". Somebody who
    has just given a first name and nothing else needs to know what is still
    owed, and "incomplete" is a status they can already read on the line above.
    """
    profile: Profile | None = standing.profile

    print(f"user_id: {standing.user_id}")
    print(f"tier: {standing.tier.value}")
    if profile is None:
        # The ordinary state, not an error - see ``ProfileStanding``. Every
        # account that predates this command is in exactly this state, and
        # ``profile`` has to be readable by them or it is not readable at all.
        print("profile: none given yet")
    else:
        print(f"display name: {profile.display_name}")
        print(f"first name: {_given(profile.legal_first_name)}")
        print(f"last name: {_given(profile.legal_last_name)}")
        print(f"date of birth: {_given(profile.date_of_birth)}")
        print(f"phone: {_given(profile.phone)}")
        print(f"country: {_given(profile.country)}")
        print(f"address: {_given(profile.address_line)}")

    if not standing.is_complete:
        # Spelled from the enum rather than typed out, so this sentence cannot
        # disagree with the tier it is describing.
        print(
            f"note: the tier becomes {Tier.IDENTIFIED.value} once a legal first "
            "and last name, a date of birth, a phone number and a country are "
            "all given"
        )

    print("limits:")
    for currency in Currency:
        limits = limits_for(standing.tier, currency)
        print(
            f"  {currency.value}  "
            f"per transaction {_ceiling(limits.per_transaction)}  "
            f"daily outflow {_ceiling(limits.daily_outflow)}  "
            f"max balance {_ceiling(limits.max_balance)}"
        )


def _profile(args, factory, actor: User) -> int:
    """Print this account's details, its tier, and what that tier allows.

    The terminal's ``GET /users/me/profile``, and it prints the ceilings rather
    than only the details on purpose. The reason to ask who this system thinks
    you are is to find out what you are therefore allowed to move, and a person
    who learns their limit by being refused has learned it too late.

    No wallet id and no amount, unlike every money command below it, because
    nothing here is about money: one row is read and printed.
    """
    service = build_profile_service(
        unit_of_work_factory=factory, actor=actor.user_id
    )
    _print_standing(service.standing())
    return 0


def _set_profile(args, factory, actor: User) -> int:
    """Give or change this account's details, leaving unmentioned fields alone.

    The terminal's ``PUT /users/me/profile`` with one deliberate difference, and
    the difference is a terminal's: **every flag is optional and omitting one
    keeps the value already stored.** The HTTP body carries all seven fields, so
    a ``PUT`` cannot be ambiguous - but a command line is typed from memory, and
    the failure mode of a whole-replacement version here is that
    ``set-profile --display-name Ada`` would clear six fields the person never
    mentioned and drop them a tier. Merging before the call costs a read and
    removes that failure. What reaches ``ProfileService.save`` is still the whole
    profile, still validated by ``Profile.__post_init__`` in one place, still
    replaced wholesale - the merge is this function's, not the domain's.

    The three states a flag can be in are ``_NOT_PASSED`` (not typed at all -
    keep what is stored), an empty string (clear the field), and a value (set
    it). See ``_NOT_PASSED`` for why the first cannot be spelled ``None``.

    **The read and the write are two units**, and this is the one place in the
    feature where that is worth stating plainly rather than defended: the merge
    is computed from a profile read a moment before the call that stores it, so
    two ``set-profile`` invocations racing in one terminal window can lose one of
    the two edits. The HTTP path cannot, because its body carries every field.
    What is *not* at risk is a half-applied profile - the write itself is a
    single validated replacement inside one unit, so a refusal leaves the stored
    row exactly as it was, and a merge computed from a stale row is a whole
    profile that happens to be slightly old rather than a torn one.

    There is no ``--tier``, and there is no method below that could take one: a
    tier follows from which of these fields are filled in. Giving details is the
    only thing that raises one.
    """
    service = build_profile_service(
        unit_of_work_factory=factory, actor=actor.user_id
    )
    current: Profile | None = service.standing().profile

    if current is None and args.display_name is _NOT_PASSED:
        # The one flag that is required, and only on the first invocation - the
        # condition argparse cannot state. See the parser, above.
        raise CliError(
            "no profile yet, so --display-name is required: a display name is "
            "the one field a profile must have, and a profile with nothing in "
            "it would be a row that exists to say nothing"
        )

    def kept(value, field):
        """What to store for one field: what was passed, or what is already there.

        ``field`` is the aggregate's own attribute name, which is what makes
        this short - the six optional fields differ in nothing but their name
        here, and six hand-written ternaries would be six chances to point one
        of them at the wrong attribute.
        """
        if value is not _NOT_PASSED:
            return value
        return None if current is None else getattr(current, field)

    standing = service.save(
        display_name=kept(args.display_name, "display_name"),
        legal_first_name=kept(args.first_name, "legal_first_name"),
        legal_last_name=kept(args.last_name, "legal_last_name"),
        date_of_birth=kept(args.birth_date, "date_of_birth"),
        phone=kept(args.phone, "phone"),
        country=kept(args.country, "country"),
        address_line=kept(args.address, "address_line"),
        # The one clock reading for this invocation, at the boundary - the same
        # line ``_current_actor`` draws and for the same reason. It becomes
        # ``updated_at``, and ``created_at`` on a first save.
        now=datetime.now(),
    )
    print(f"saved profile for {standing.user_id}")
    _print_standing(standing)
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
    """Run ``deposit`` or ``withdraw``.

    **One branch, and it is the whole point of the feature.** ``deposit`` goes
    straight to the service as it always did; ``withdraw`` records a request,
    shows it to the person, asks, and answers it. The split is written out here
    rather than hidden behind a flag on ``OPERATIONS``, because the two commands
    now have genuinely different shapes and a shared body would have to be told
    which one it was anyway - one line further from the place a reader looks.

    **The preview is arithmetic, not a promise.** It shows what the balance would
    become if this went through, computed from the wallet that was just read - and
    when the amount is larger than the available balance it says so in words
    instead of printing a negative one, which would be a number no wallet can
    hold. The wallet still decides at execution: this line is a courtesy to the
    person reading it, and a refusal still arrives from the domain with the
    domain's own words.

    **Nothing printed here is taken from the command line.** The preview and the
    result line are both read off the *record*, because the record is what will
    be carried out. That matters in the one case where they can differ: a
    ``--ref`` that was already used returns the request it already names, whose
    amount may not be the one just typed. Printing the typed amount would be
    printing a movement that is not the movement about to happen.
    """
    wallet = service.get_wallet(args.wallet_id)
    amount = Money(args.amount, wallet.currency)

    if args.command not in _CONFIRMED_OPERATIONS:
        service.deposit(
            args.wallet_id,
            amount,
            args.ref if args.ref is not None else str(uuid.uuid4()),
        )
        current = service.get_wallet(args.wallet_id)
        print(
            f"{OPERATIONS[args.command]} {amount} | "
            f"available {current.available_balance} | "
            f"locked {current.locked_balance}"
        )
        _deliver_after(factory, settings, deferred_reason)
        return 0

    now = datetime.now()
    requested = service.request_confirmation(
        args.wallet_id,
        ConfirmationKind.WITHDRAWAL,
        now,
        internal_reference=args.ref,
        amount=amount,
    )
    confirmation = requested.confirmation

    # Read off the record, not off ``amount`` - see the docstring. After a
    # reference collision these are different numbers, and the record's is the
    # one that will move.
    moving = confirmation.amount
    print(
        f"about to withdraw {moving} from wallet {confirmation.wallet_id}"
    )
    if moving > wallet.available_balance:
        print(
            f"  this is more than the available balance of "
            f"{wallet.available_balance} - it will be refused"
        )
    else:
        print(
            f"  available {wallet.available_balance} -> "
            f"{wallet.available_balance - moving}"
        )
    print("  this moves money out of your wallet for good")
    if not _confirmed("confirm?", args.yes):
        return 0

    service.confirm(confirmation.confirmation_id, datetime.now())
    current = service.get_wallet(args.wallet_id)
    print(
        f"{OPERATIONS[args.command]} {moving} | "
        f"available {current.available_balance} | "
        f"locked {current.locked_balance}"
    )
    _deliver_after(factory, settings, deferred_reason)
    return 0


def _payout(service: WalletService, args, factory, settings, deferred_reason) -> int:
    """Send money out to a bank account, after asking.

    Same two-step shape as ``withdraw`` above, and the same rule about where the
    printed numbers come from: everything shown to the person, and everything
    reported afterwards, is read off the record that will be carried out.

    ``as_of`` is read at the moment of confirming rather than of requesting, and
    that ordering is deliberate. A payout may only spend pots that have come due,
    and a person may sit at the prompt long enough for a pot to mature - so
    judging maturity at the moment the money moves is both the more useful answer
    and the one that matches what the wallet will actually do.
    """
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

    requested = service.request_confirmation(
        args.wallet_id,
        ConfirmationKind.PAYOUT_FROM_LOCKED,
        datetime.now(),
        internal_reference=args.ref,
        amount=amount,
        destination=destination,
        # The pot, if one was named. Omitted, this is the pooled draw - and the
        # omission is honest rather than lazy: a human who does not know which
        # pot they mean has not committed anything, and the wallet spends the
        # matured pots oldest first, exactly as it always has.
        #
        # Note what is *not* passed alongside it: nothing here says when a
        # commitment to pay was made. This is a hand-typed payment, so it has
        # none, and a sealed business pot will refuse it on that ground alone.
        fund_name=args.fund,
    )
    confirmation = requested.confirmation
    moving = confirmation.amount
    paying = confirmation.destination

    print(f"about to pay {moving} to {paying} from wallet {confirmation.wallet_id}")
    if confirmation.fund_name is None:
        print("  drawn from the matured pots, oldest first")
    else:
        print(f"  drawn from the pot {confirmation.fund_name!r}")
    print("  this moves money out of your wallet for good")
    if not _confirmed("confirm?", args.yes):
        return 0

    service.confirm(confirmation.confirmation_id, datetime.now())
    current = service.get_wallet(args.wallet_id)
    print(
        f"paid {moving} to {paying} | "
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


def _reconcile_command(args, factory, settings) -> int:
    """Wire up and run one reconciliation pass, with no actor anywhere in the call.

    **The second command in the CLI that runs as nobody**, and it sits beside
    ``_plan_tick_command`` for the reason that function gives: this one's whole
    argument list is missing the ``actor`` every other command handler takes, and
    the layout is the clearest way to say what it is. The difference between the
    two is worth stating, because "actorless" is not one property:

    ``plan tick`` is actorless because it serves *everybody* - it mints an
    executor per plan, owned by that plan's user. This is actorless because it
    serves nobody in particular and reads no wallet at all: it hands a reference
    to ``SettlePayment`` and the settler derives the owner from the ledger row,
    so the ownership of the money this run credits is decided inside the use case
    that already had to decide it. There is no closure here and nothing to build
    per record.

    **A missing key is a note and an exit 0**, which is the tick's
    missing-mailbox rule one provider over. With no ``PAYSTACK_SECRET_KEY``
    there is nothing to ask with, and a job that failed loudly in that state
    would be a cron line that mailed an operator every hour about a deployment
    that simply does not take card payments. It says so once - so that somebody
    who *expected* reconciliation to be running finds out it is not - and stops.

    ``--as-of`` is threaded straight through to the use case rather than being
    read here, because the grace window is judged against it and the use case is
    what owns that policy.
    """
    paystack = paystack_from_environment()
    if paystack is None:
        # The variable is named, which is ``describe_configuration``'s rule one
        # provider over: the failure mode of a job with nothing to ask with is
        # silence, and "PAYSTACK_SECRET_KEY is not set" is the difference between
        # a two-minute fix and an afternoon of guessing.
        print(
            f"note: nothing to reconcile as of {_moment(args.as_of)} - "
            f"PAYSTACK_SECRET_KEY is not set (see 'Running the scheduler' "
            f"in the README)"
        )
        return 0

    reconciler = build_reconciler(
        unit_of_work_factory=factory, settings=settings, paystack=paystack
    )
    _report_reconciliation(reconciler.execute(args.as_of))
    return 0


def _report_reconciliation(report) -> None:
    """Print one line per row the run asked about, and one when it found none.

    **A quiet run still speaks**, unlike ``_report_delivery`` which stays silent
    when there is nothing to do. The two rules are the opposite and both are
    right: a drain with an empty queue has nothing an operator needs to know,
    while a reconciler with nothing in flight has *the* thing an operator wants
    to know - that the webhook path is keeping up. "Nothing was in flight" and
    "this job is not running" look identical from cron's output otherwise.

    The rows that were too young and the rows left for the next run are a single
    clause at the end, and they are counts rather than lines because neither is
    actionable on its own: a payment started two minutes ago is not news, and a
    backlog is only interesting as a trend.
    """
    if not report.reconciled:
        print("nothing in flight")
    else:
        for one in report.reconciled:
            print(f"{one.reference}  {one.outcome.value}  {one.detail}")

    if report.too_young or report.remaining:
        print(
            f"note: {report.too_young} too young to ask about, "
            f"{report.remaining} left for the next run"
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
    # One read of the SMS configuration, and it is a *second* read rather than part
    # of the one above because the two installations are configured separately: an
    # operator may have SMTP and no Termii, or the reverse, and a single merged
    # settings object would have to invent a way to say which half was missing.
    # There is no ``deferred_reason`` beside it, and the asymmetry is the point:
    # a missing mail account is a thing this installation works around, and a
    # missing SMS account is a refusal composed by the builder that needs one - see
    # ``_signup_phone``.
    termii = termii_from_environment()
    try:
        # --- the commands that need nobody ----------------------------------
        #
        # Dispatched before any actor is resolved, and that ordering is the
        # design rather than an optimisation. The three identity commands cannot
        # need a session - they are how a session comes to exist, or cease to -
        # ``confirm-email`` must not need one either, because the code it prompts
        # for was mailed to the address being moved to and is a better warrant
        # than the session that asked for the change, ``plan tick`` must not,
        # because it serves the whole installation and there is no person it could
        # act as, and ``reconcile`` must not either, because it asks a payment
        # provider about payments that belong to whoever made them. Resolving an
        # actor first, as this function used to do unconditionally, would have
        # made a scheduler that requires a login - and an address change that
        # cannot be answered from a second machine.
        #
        # The two password-reset commands are here for the strongest version of
        # that argument: ``reset-password`` cannot resolve an actor because the
        # person running it is locked out, and ``confirm-password-reset`` resolves
        # none because answering the mail is what proves the account - and because
        # it *ends every session*, so demanding a live one would be deleting the
        # credential it had just required.
        #
        # The two phone commands are here because the account they are about does
        # not exist yet: ``signup-phone`` is run by somebody who may hold no account
        # at all, and ``confirm-phone`` is what creates one. There is no token for
        # either to read, which is what makes this the one flow in the file whose
        # subject is a number rather than a person.
        if args.command == "signup":
            return _signup(args, factory)
        if args.command == "signup-phone":
            return _signup_phone(args, factory, termii)
        if args.command == "confirm-phone":
            return _confirm_phone(args, factory)
        if args.command == "login":
            return _login(args, factory)
        if args.command == "login-phone":
            return _login_phone(args, factory)
        if args.command == "logout":
            return _logout(args, factory)
        if args.command == "confirm-email":
            return _confirm_email(args, factory, settings)
        if args.command == "reset-password":
            return _reset_password(args, factory, settings)
        if args.command == "confirm-password-reset":
            return _confirm_password_reset(args, factory, settings)
        if args.command == "plan" and args.plan_command == "tick":
            return _plan_tick_command(args, factory, settings, deferred_reason)
        if args.command == "reconcile":
            return _reconcile_command(args, factory, settings)

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
        # Next to ``whoami`` because they are the same question asked one step
        # further in - it answers which account this is, these answer who holds
        # it. Neither takes ``service``: it is built above for the commands that
        # move money, and these two move none, so each builds the one service it
        # needs instead. See ``ProfileService``.
        if args.command == "profile":
            return _profile(args, factory, actor)
        if args.command == "set-profile":
            return _set_profile(args, factory, actor)
        if args.command == "change-email":
            return _change_email(
                args, factory, settings, actor, deferred_reason
            )
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
