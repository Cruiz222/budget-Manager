"""Where the settings come from, and why it is the environment.

**This is the only module in the codebase that reads ``os.environ``.** Everywhere
else takes what it needs as an argument, which is what makes the rest of the
system testable without a patched environment and what keeps a configuration
decision out of the domain. The reading is concentrated here so there is exactly
one place to look when something is configured wrong - when a message does not
arrive, or when a server is talking to the wrong database.

It holds six unrelated things - mail settings, a payment provider's secret, an
SMS provider's key and sender id, a Google client id, a database path and a
session path - and that is a consequence of the sentence above rather than a
failure of tidiness. The
alternative, a second module reading ``os.environ`` for the database or for
Termii, would make the promise false, and the promise is the thing that makes
configuration auditable. This module was ``notifications.email_settings`` until
the API needed a database path; the *reader* is still singular, which is the
property worth keeping, and only the name widened to say so.

The environment rather than the database, for two reasons and only one of them is
about security:

- **The SMTP password cannot live in ``budget.db``.** A database file gets
  copied, backed up and committed by accident; a password in one is a password
  leaked. Given that the credential has to come from somewhere outside anyway,
  the address travels with it - one boundary to configure, one boundary to audit.
  ``PAYSTACK_SECRET_KEY`` is the same argument with more at stake: it is the key
  that signs webhooks, so a copy of it in a committed file is a copy of the
  ability to tell this server that money arrived. ``TERMII_API_KEY`` is the same
  argument with a bill attached: a copy of it is a copy of this account's ability
  to spend, one text at a time, from anywhere on the internet.

  **``GOOGLE_CLIENT_ID`` is the one setting here that is not a credential, and
  it is deliberately still in this module.** It is public by construction - it
  travels in every browser-side authorization request and is visible to anybody
  who inspects the page - so it is not here because it needs hiding. It is here
  because the rule above is about *where configuration comes from*, not about
  secrecy: this module is the one place a deployment is described, and a setting
  that lived somewhere else would be a setting nobody thinks to look for. The
  consequence to be aware of is the opposite of the paragraph above: a client id
  in a committed file leaks nothing, so the temptation to treat it casually is
  harmless - while the file it sits in must still never hold the secret that
  would.
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

**A missing payment key is the same shape of state with the opposite safe
default**, and that difference is the reason it gets a reader of its own rather
than sharing mail's. An install with no mail silently says nothing; an install
with no payment key must silently say *no* - there is nothing to verify a webhook
against, so accepting one would be accepting a stranger's word that money
arrived. ``paystack_from_environment`` returning ``None`` is what lets the
webhook route tell "unconfigured" from "unproven" and answer them differently.
See that route for why those two are a 503 and a 401 rather than one error.
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


@dataclass(frozen=True)
class PaystackSettings:
    """Everything one conversation with Paystack needs, and nothing else.

    One field, and it is deliberately one rather than a key/secret pair. Paystack
    authenticates with a single secret key, and this is the only secret the
    integration holds: the public key is a browser-side value that this server
    has no use for, and adding a field for it would be a field somebody
    eventually fills in with the wrong one.

    Frozen and behaviour-free, exactly as ``EmailSettings`` is: it opens no
    connection, signs nothing, and can be built by hand in a test without going
    near the environment. The signing itself lives in the adapter, because that
    is where the algorithm belongs - a settings object that knew how to compute
    an HMAC would be a settings object with an opinion about a wire format.

    ``secret_key`` is *reported* by nothing. There is no ``__repr__`` override
    here even though the default dataclass one would print it, because the
    places this value is logged are already controlled: it is passed to the
    adapter and otherwise never travels. Rewriting ``__repr__`` to hide it would
    be worth doing the day something starts logging whole settings objects, and
    pretending to have done it now would be the worse mistake - a redacted
    ``repr`` reads as "this type is safe to print", which is a claim nothing here
    has earned.
    """

    secret_key: str


def paystack_from_environment(environ=None) -> PaystackSettings | None:
    """Build the payment settings from the environment, or ``None`` if unset.

    Shaped exactly like ``from_environment``, and the resemblance is the point:
    two readers with two different conventions would be two things to remember,
    and the one that was remembered less often would be the one that mattered.

    ``None`` means this installation cannot take payments, which is a legitimate
    state - a developer running the suite, a fresh clone, a deployment that has
    not been configured yet - and *not* an error. What the caller does with it is
    where the two cases diverge, and the divergence is deliberate: mail's ``None``
    means "say nothing", and this one's means "accept nothing". A blank value
    counts as unset for the reason ``_text`` gives everywhere else - a variable
    set to spaces is a variable somebody meant to fill in, and treating it as a
    key would mean verifying every signature against a key of whitespace, which
    is exactly the shape of a bug that looks like a working integration until
    someone tries it.
    """
    environ = os.environ if environ is None else environ
    secret_key = _text(environ, "PAYSTACK_SECRET_KEY")
    if secret_key is None:
        return None
    return PaystackSettings(secret_key=secret_key)


@dataclass(frozen=True)
class TermiiSettings:
    """Everything one conversation with Termii needs, and nothing else.

    Two fields, and both are required rather than defaulted. Termii will not send
    anything without a key, and a message with no sender id is refused - so a
    settings object holding one of the two would describe a conversation that
    cannot happen, and the reader below would have to return it anyway. Returning
    ``None`` for a half-filled environment is the honest answer: the install
    cannot send a text, which is exactly what ``None`` means everywhere in this
    module.

    Frozen and behaviour-free, exactly as ``EmailSettings`` and
    ``PaystackSettings`` are: it opens no connection, signs nothing and can be
    built by hand in a test without going near the environment. The request itself
    lives in the adapter, because a settings object that knew how to build one
    would be a settings object with an opinion about a wire format.

    ``api_key`` is *reported* by nothing, and the paragraph ``PaystackSettings``
    writes about deliberately not overriding ``__repr__`` applies here word for
    word - a redacted ``repr`` reads as "this type is safe to print", which is a
    claim nothing here has earned.

    ``sender_id`` is the name a recipient sees instead of a number, and it is a
    fact about the account rather than about this code: Termii requires it to have
    been registered and approved before it will send under it, which is why a
    sender id that is wrong fails as a provider refusal rather than as a local
    one. The adapter checks its *length* and nothing else, because length is the
    only part of that which is knowable from here.
    """

    api_key: str
    sender_id: str


#: The Termii variables with no sensible default, in the order a person should
#: set them. Shaped like ``REQUIRED`` above rather than like the payment reader's
#: single field, because this is the mail half's case and not the payment one's:
#: two variables, and a missing one has a name worth telling somebody.
TERMII_REQUIRED = ("TERMII_API_KEY", "TERMII_SENDER_ID")


def describe_termii_configuration(environ=None) -> str | None:
    """Why there is no SMS configuration, in words a user can act on.

    ``describe_configuration``'s shape and its argument, one channel over: the
    failure mode of a misconfigured notifier is silence, and "TERMII_API_KEY is
    not set" is the difference between a two-minute fix and an afternoon of
    guessing. Returns ``None`` when the configuration is complete.

    The sentence is composed here rather than at the raise site for the reason the
    mail one is: this module is the only reader of the environment, so the use case
    that refuses cannot write it, and the CLI and the API have to render one
    identical sentence. What travels into the domain is a string, not a reader.
    """
    environ = os.environ if environ is None else environ

    for name in TERMII_REQUIRED:
        if _text(environ, name) is None:
            return f"{name} is not set"

    return None


def termii_from_environment(environ=None) -> TermiiSettings | None:
    """Build the SMS settings from the environment, or ``None`` if unset.

    The third reader of this exact shape - ``from_environment`` for mail,
    ``paystack_from_environment`` for payments, this one for texts - and the
    resemblance is now the point of the pattern rather than a coincidence between
    two of them. Three conventions would be three things to remember, and the one
    remembered least often would be the one that mattered.

    ``None`` means this installation cannot send a text, which is a normal state -
    a fresh clone, this test suite, a deployment nobody has configured yet - and
    not an error. What it means to a caller differs from the other two, and the
    difference is the reason all three are written out rather than merged: mail's
    ``None`` says "carry on and say nothing", the payment one's says "accept
    nothing", and this one says "this flow cannot run at all". A signup that
    cannot text a code has no fallback the way a warning does and no safe default
    the way a webhook does; it has to refuse, in words naming the variable, which
    is what ``describe_termii_configuration`` above is for.
    """
    environ = os.environ if environ is None else environ
    if describe_termii_configuration(environ) is not None:
        return None

    return TermiiSettings(
        api_key=_text(environ, "TERMII_API_KEY"),
        sender_id=_text(environ, "TERMII_SENDER_ID"),
    )


@dataclass(frozen=True)
class GoogleSettings:
    """Everything one Google sign-in needs, and nothing else.

    **One field, and there is no second one to add - no client secret.** That is
    a property of the flow this system uses rather than an omission, and it is
    worth stating plainly because every other integration in this module has a
    secret and a reader will expect one here. Nothing in this codebase exchanges
    an authorization code with Google: the client obtains an id_token itself and
    presents it, so this server only ever *verifies* something Google signed. The
    secret exists for the half of OAuth that asks Google for a token in exchange
    for a code, and that half is not built - see the README's Phase 4, where the
    redirect flow lands with the browser front-end.

    So ``client_id`` is the whole of the configuration, and it is an *audience*
    rather than a credential: it is what this server compares a token's ``aud``
    claim against, to establish that the token was minted for this application
    and not for some other one that also integrates Google. Without that
    comparison every Google token on the internet would be a credential here,
    which is why the field is required rather than optional - a reader that
    returned settings without it would be a reader that turned verification into
    a formality.

    Frozen and behaviour-free, exactly as ``EmailSettings``, ``PaystackSettings``
    and ``TermiiSettings`` are: it opens no connection, verifies nothing and can
    be built by hand in a test without going near the environment. The JWKS
    fetch, the signature check and the claim inspection all live in the adapter,
    because a settings object that knew how to verify a JWT would be a settings
    object with an opinion about a wire format.
    """

    client_id: str


#: The Google variables with no sensible default, spelled as a tuple for
#: ``REQUIRED``'s and ``TERMII_REQUIRED``'s reason: the message a person reads
#: when this is unset names the variable, and a name is only worth composing if
#: there is somewhere for it to come from.
GOOGLE_REQUIRED = ("GOOGLE_CLIENT_ID",)


def describe_google_configuration(environ=None) -> str | None:
    """Why there is no Google configuration, in words a user can act on.

    ``describe_termii_configuration``'s shape and its argument one channel over,
    and the reason to keep them identical is the reason to have the function at
    all: the sentence is composed **here** rather than at the raise site because
    this module is the only reader of the environment, so the use case that
    refuses cannot write it and the CLI and the API have to render one identical
    sentence. What travels into the domain is a string, never a reader.

    Returns ``None`` when the configuration is complete.
    """
    environ = os.environ if environ is None else environ

    for name in GOOGLE_REQUIRED:
        if _text(environ, name) is None:
            return f"{name} is not set"

    return None


def google_from_environment(environ=None) -> GoogleSettings | None:
    """Build the Google settings from the environment, or ``None`` if unset.

    The fourth reader of this exact shape - ``from_environment`` for mail,
    ``paystack_from_environment`` for payments, ``termii_from_environment`` for
    texts, this one for Google sign-ins - and at four the pattern is no longer
    something a reader has to infer from three examples.

    ``None`` means this installation cannot accept a Google sign-in, which is a
    normal state - a fresh clone, this test suite, a deployment nobody has
    configured yet - and not an error. **It means the flow cannot run at all**,
    which puts it with Termii's ``None`` rather than mail's: there is no
    equivalent of "say nothing" or "accept nothing" available here, because a
    token that cannot be verified is not a degraded sign-in but an unverified
    one, and the only safe answer to that is to refuse. The refusal names the
    variable - that is what ``describe_google_configuration`` above is for - so
    an operator pointing this at a real deployment learns what to set rather than
    that something went wrong.

    A blank value counts as unset, for ``_text``'s reason everywhere else: a
    variable set to spaces is one somebody meant to fill in, and treating it as a
    client id would mean comparing every token's audience against whitespace -
    which is a configuration that rejects every sign-in while looking, in any
    config listing, like it is set.
    """
    environ = os.environ if environ is None else environ
    if describe_google_configuration(environ) is not None:
        return None

    return GoogleSettings(client_id=_text(environ, "GOOGLE_CLIENT_ID"))


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
