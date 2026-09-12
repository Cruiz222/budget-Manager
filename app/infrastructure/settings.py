"""Where the settings come from, and why it is the environment.

**This is the only module in the codebase that reads ``os.environ``.** Everywhere
else takes what it needs as an argument, which is what makes the rest of the
system testable without a patched environment and what keeps a configuration
decision out of the domain. The reading is concentrated here so there is exactly
one place to look when something is configured wrong - when a message does not
arrive, or when a server is talking to the wrong database.

It holds three unrelated things - mail settings, a database path and a session
path - and that is a consequence of the sentence above rather than a failure of
tidiness. The alternative, a second module reading ``os.environ`` for the
database, would make the promise false, and the promise is the thing that makes
configuration auditable. This module was ``notifications.email_settings`` until
the API needed a database path; the *reader* is still singular, which is the
property worth keeping, and only the name widened to say so.

The environment rather than the database, for two reasons and only one of them is
about security:

- **The SMTP password cannot live in ``budget.db``.** A database file gets
  copied, backed up and committed by accident; a password in one is a password
  leaked. Given that the credential has to come from somewhere outside anyway,
  the address travels with it - one boundary to configure, one boundary to audit.
- **Configuration is not data.** Nothing in the database is a *setting*; it is
  the user's money and plans. A row that changed what the software did would be
  the first of its kind, and it would need its own interface, its own migration
  and its own validation for no gain.

**Missing mail configuration is a normal state, not an error.** A fresh install
has no mail account, and ``from_environment`` says so by returning ``None``. The
tick still runs, still warns, and still pays - it simply has nowhere to send. The
alternative, raising, would mean a user who has not set up email cannot use the
scheduler at all. ``database_path`` has no equivalent state: a database is not
optional, so it has a default rather than a ``None``.
"""

import os
from dataclasses import dataclass

#: The variables with no sensible default. A missing one means unconfigured.
REQUIRED = ("SMTP_HOST", "BUDGET_NOTIFY_TO")

#: The default SMTP submission port. 587 is the one that speaks STARTTLS.
DEFAULT_PORT = 587

#: The database file a fresh install uses, in the working directory.
#:
#: Named here rather than in the CLI, because two presentations now need it - the
#: CLI's ``--db`` argument defaults to this, and the API reads it from the
#: environment - and a default written down twice is a default that can disagree
#: with itself.
DEFAULT_DATABASE_PATH = "budget.db"

#: Where the CLI keeps the session token it is logged in with.
#:
#: Under the user's config directory rather than beside the database, and the
#: difference is the point: ``budget.db`` is *the data*, and this is *a
#: credential for one person using one machine*. Two people sharing a database
#: over a network share - or one person with the project checked out twice -
#: must not share a login, and anything sitting next to the database invites
#: exactly that. ``~/.config`` is also where the platform expects per-user state
#: to live, which means it is backed up, ignored by version control and removed
#: on logout by the habits people already have.
#:
#: Kept with the tilde unexpanded, so the path reads here the way it will be
#: written in the README and in ``BUDGET_SESSION``, and expanded at the point of
#: use where a wrong guess is visible.
DEFAULT_SESSION_PATH = "~/.config/budget/session"


@dataclass(frozen=True)
class EmailSettings:
    """Everything one SMTP conversation needs, and nothing else.

    Deliberately a plain frozen value rather than a settings *service*: it holds
    no behaviour, opens no connection, and can be constructed by hand in a test
    without touching the environment at all.
    """

    host: str
    port: int
    sender: str
    recipient: str
    username: str | None = None
    password: str | None = None
    #: Whether to upgrade the connection with STARTTLS before sending.
    #:
    #: On by default, and it must be explicitly switched *off* rather than
    #: silently fallen back from. Tolerating a server that does not offer
    #: STARTTLS would mean accepting a downgrade to plaintext on a connection
    #: carrying a password - and an attacker who can strip the capability from
    #: the server's greeting can make that happen. A local debugging server has
    #: no certificate, so it needs this set to ``0``; that is a deliberate act,
    #: not something the code decides on the user's behalf.
    starttls: bool = True


def _text(environ, name: str) -> str | None:
    """The value of a variable, or ``None`` if it is absent or blank."""
    value = environ.get(name, "").strip()
    return value or None


def _flag(environ, name: str, default: bool) -> bool:
    """A boolean setting, spelled as "1"/"0"/"true"/"false"."""
    value = _text(environ, name)
    if value is None:
        return default
    return value.lower() not in ("0", "false", "no", "off")


def describe_configuration(environ=None) -> str | None:
    """Why there is no email configuration, in words a user can act on.

    Returns ``None`` when the configuration is complete. Being able to say
    *which* variable is missing matters more than it looks: the failure mode of
    a misconfigured notifier is silence, and "SMTP_HOST is not set" is the
    difference between a two-minute fix and an afternoon of guessing.
    """
    environ = os.environ if environ is None else environ

    for name in REQUIRED:
        if _text(environ, name) is None:
            return f"{name} is not set"

    port = _text(environ, "SMTP_PORT")
    if port is not None:
        try:
            int(port)
        except ValueError:
            return f"SMTP_PORT is not a number: {port!r}"

    if _text(environ, "BUDGET_NOTIFY_FROM") is None and _text(
        environ, "SMTP_USER"
    ) is None:
        return "SMTP_USER or BUDGET_NOTIFY_FROM is not set"

    return None


def from_environment(environ=None) -> EmailSettings | None:
    """Build settings from the environment, or ``None`` if it is incomplete.

    ``environ`` is injectable so tests can pass a plain dict; production passes
    nothing and gets ``os.environ``. That is what keeps this module the single
    reader of the real environment while still being testable without
    monkeypatching.
    """
    environ = os.environ if environ is None else environ
    if describe_configuration(environ) is not None:
        return None

    sender = _text(environ, "BUDGET_NOTIFY_FROM") or _text(environ, "SMTP_USER")
    return EmailSettings(
        host=_text(environ, "SMTP_HOST"),
        port=int(_text(environ, "SMTP_PORT") or DEFAULT_PORT),
        sender=sender,
        recipient=_text(environ, "BUDGET_NOTIFY_TO"),
        username=_text(environ, "SMTP_USER"),
        password=_text(environ, "SMTP_PASSWORD"),
        starttls=_flag(environ, "SMTP_STARTTLS", default=True),
    )


def database_path(environ=None) -> str:
    """Where the SQLite database lives.

    ``BUDGET_DB`` when it is set to something non-blank - a variable set to
    ``""`` or to spaces is a variable somebody meant to fill in, and treating it
    as a path would produce a database called nothing at all - and
    ``DEFAULT_DATABASE_PATH`` otherwise.

    There is no unconfigured state here, unlike ``from_environment``, and the
    difference is not an oversight: an install with no mail account can still
    work, and an install with no database has nothing to work on. So the absent
    case is a default rather than a ``None``, and the return type says so.

    ``environ`` is injectable for the same reason ``from_environment``'s is, and
    it is the same argument rather than a coincidence: this module being the one
    reader of the real environment is only a useful property if the things it
    reads can also be handed to it.
    """
    environ = os.environ if environ is None else environ
    return _text(environ, "BUDGET_DB") or DEFAULT_DATABASE_PATH


def session_path(environ=None) -> str:
    """Where the CLI keeps its session token.

    ``BUDGET_SESSION`` when it is set to something non-blank, and
    ``DEFAULT_SESSION_PATH`` otherwise - the same absent-means-default shape as
    ``database_path``, and for the same reason: there is a state in which you are
    not logged in, but that state is *an absent file*, not an unconfigured
    setting. Somewhere to look for a token is always known.

    **Both paths go through ``expanduser``**, the configured one as well as the
    default. A shell expands ``~`` in ``export BUDGET_SESSION=~/token``, but a
    systemd unit, a Docker ``--env-file`` and a ``.env`` read by anything but a
    shell all pass the tilde through literally - and the failure that follows is
    quiet in the worst way: a file named ``~`` appears in the working directory,
    login reports success, and the next command cannot find the session. It
    cannot break a legitimate path either, since a tilde is only special as the
    first character, which is exactly the case being handled.

    Only the CLI reads this. The API takes its token from the request and has
    no business knowing where a client keeps one, so there is no server-side
    equivalent of this setting and there should not be.

    ``environ`` is injectable for the same reason every other reader here takes
    it, and this function exists rather than a ``os.environ.get`` at the CLI call
    site for the reason the module docstring gives: one reader, or the promise
    is false.
    """
    environ = os.environ if environ is None else environ
    return os.path.expanduser(
        _text(environ, "BUDGET_SESSION") or DEFAULT_SESSION_PATH
    )
