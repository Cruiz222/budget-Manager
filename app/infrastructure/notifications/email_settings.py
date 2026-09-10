"""Where the notification settings come from, and why it is the environment.

**This is the only module in the codebase that reads ``os.environ``.** Everywhere
else takes what it needs as an argument, which is what makes the rest of the
system testable without a patched environment and what keeps a configuration
decision out of the domain. The reading is concentrated here so there is exactly
one place to look when a message does not arrive.

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

**Missing configuration is a normal state, not an error.** A fresh install has no
mail account, and ``from_environment`` says so by returning ``None``. The tick
still runs, still warns, and still pays - it simply has nowhere to send. The
alternative, raising, would mean a user who has not set up email cannot use the
scheduler at all.
"""

import os
from dataclasses import dataclass

#: The variables with no sensible default. A missing one means unconfigured.
REQUIRED = ("SMTP_HOST", "BUDGET_NOTIFY_TO")

#: The default SMTP submission port. 587 is the one that speaks STARTTLS.
DEFAULT_PORT = 587


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
