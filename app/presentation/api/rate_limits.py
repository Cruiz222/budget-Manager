"""What the limits are, and the argument for each number.

**Policy lives here rather than in the route modules, and the reason is that a limit
is a statement about the product rather than about an endpoint.** ``routes/sessions.py``
knows how to log somebody in; it does not know how many times an hour that is
reasonable, and a reader asking "what stops a stranger spending this server's money"
should be able to answer it from one file instead of by opening seven. It is also the
only way the numbers can be compared with each other, which is the thing that keeps a
table of limits coherent: a per-address budget of three an hour is meaningless without
the address budget beside it being ten a quarter hour.

**Every number here is a policy choice and none of them is derived from a
measurement**, because no measurement exists yet - this installation has never been
abused or heavily used. They are set where a legitimate person is not inconvenienced
and an automated caller is, which is the only defensible starting position, and they
are expected to move once there is a real traffic record to move them from. What is
*not* expected to move is the shape: a per-subject budget and an installation ceiling,
each with a written reason.

**The ceiling is not a second mechanism.** It is the same bucket machinery with the
subject fixed to ``INSTALLATION`` - one code path, two keys - so there is no separate
"global limiter" to keep in step with the per-subject one.

**The two dimensions count different things, deliberately.** The subject dimension
counts *attempts by that subject*, so a subject's budget is spent by that subject's
own requests and by nothing else. The ceiling counts *work this installation admitted*,
so it is charged only once the subject dimension has let a request through. The
asymmetry is the point and it is worth being explicit about, because the natural
reading of "two limits" is "both count everything":

- A subject that has exhausted its own budget does **not** consume the installation's,
  however hard it keeps trying. So one abusive caller cannot lock the feature for
  everybody else just by hammering - which is the failure a naive shared counter
  produces, and the reason this ordering was chosen rather than the reverse.
- A request refused by the ceiling has already spent a subject credit. That is the
  cost of the ordering above and it is harmless: the caller could not have done the
  work in either case, and the credit it spent is one it will not need.

**The Google pair carries no subject budget at all**, and that is a finding rather
than an omission. Its subject would be the Google ``sub``, which is not known until the
token has been verified - and verifying it is the cost being defended. There is nothing
to key on before the money has been spent, so a ceiling is the only lever available, and
``dependencies.py`` already records these as the two routes that leave the process.

**There is no source dimension - no IP address, no ``X-Forwarded-For``.** That is not
an oversight and it is not Phase 4 laziness: until a reverse proxy this installation
controls is in front of it, every such header is a string the attacker chooses, so a
limit keyed on one would be a limit the attacker sets. The per-subject half is
meanwhile the half a CDN *cannot* do, because Cloudflare sees an IP and cannot read the
address in a request body - so when the proxy arrives and the header becomes
trustworthy, the source dimension is added to this table as a third column and the
subject dimension stays exactly as it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request

from app.infrastructure.rate_limiting import Limit
from app.presentation.api.errors import RateLimitedError

#: The subject a ceiling is counted under. A ceiling is not a different kind of thing
#: from a per-subject limit - it is the same counter with a subject every caller
#: shares, which is what keeps this file to one mechanism rather than two.
INSTALLATION = "INSTALLATION"

#: An hour, and it appears in almost every policy below. Named rather than repeated so
#: that a change to the common window is one edit, and so that the policies which
#: deliberately differ from it stand out by not saying ``_HOUR``.
_HOUR = timedelta(hours=1)
_QUARTER = timedelta(minutes=15)


@dataclass(frozen=True)
class Policy:
    """One door's limits, and why they are what they are.

    ``subject`` is ``None`` for a door where no subject can be named before the cost
    is paid - see the module docstring on the Google pair. ``ceiling`` is never
    ``None``: every limited door costs the installation something, including the ones
    where it costs a named caller something too.
    """

    subject: Limit | None
    ceiling: Limit
    why: str


POLICIES: dict[str, Policy] = {
    "sign_up": Policy(
        subject=Limit(calls=5, window=_HOUR),
        ceiling=Limit(calls=200, window=_HOUR),
        why=(
            "A person creates one account and may get their password wrong a few times "
            "doing it; five is generous for that. Each attempt costs an argon2 hash at "
            "deliberately painful parameters, so the per-address budget is what stops "
            "one address being used to burn this server's CPU."
        ),
    ),
    "sign_in": Policy(
        subject=Limit(calls=10, window=_QUARTER),
        ceiling=Limit(calls=1000, window=_HOUR),
        why=(
            "This is the door worth brute-forcing, and the budget is set to make it "
            "pointless rather than to inconvenience anyone: ten an hour against one "
            "account turns a guessing attack into something measured in years, while "
            "somebody who has genuinely forgotten which of their passwords it is still "
            "gets ten tries a quarter hour. It also bounds argon2 verifies per account, "
            "which is the CPU half of the same problem. The ceiling is the loosest here "
            "because legitimate sign-ins are the most numerous thing this API does."
        ),
    ),
    "sign_up_with_google": Policy(
        subject=None,
        ceiling=Limit(calls=300, window=_HOUR),
        why=(
            "Ceiling only - see the module docstring. What is being bounded is the "
            "outbound fetch of Google's signing keys, which this endpoint can trigger "
            "for a caller who has proved nothing."
        ),
    ),
    "sign_in_with_google": Policy(
        subject=None,
        ceiling=Limit(calls=300, window=_HOUR),
        why=(
            "Ceiling only, for the reason above and at the same number, because the two "
            "routes do the same expensive thing and differ only in what they do with "
            "the answer."
        ),
    ),
    "request_password_reset": Policy(
        subject=Limit(calls=3, window=_HOUR),
        ceiling=Limit(calls=200, window=_HOUR),
        why=(
            "This is the route a stranger can point at somebody else's account and make "
            "this server work for them: it writes a row and puts an SMTP round trip in "
            "flight, per call, and the address need not exist. Three an hour is more "
            "than enough for somebody who did not receive the first mail and wants "
            "another, and it is the difference between a mail server that sends and one "
            "that is used to send. The ceiling bounds the bill if somebody walks a list "
            "of addresses instead of hammering one."
        ),
    ),
    "request_email_change": Policy(
        subject=Limit(calls=3, window=_HOUR),
        ceiling=Limit(calls=200, window=_HOUR),
        why=(
            "The same mail round trip as a reset, and the subject is the account rather "
            "than the address because this route is behind a session and the caller is "
            "therefore known. Three an hour matches the reset because the cost to this "
            "installation is identical - and because a person who mistyped an address "
            "wants the same remedy a person who mistyped their password does."
        ),
    ),
    "request_phone_verification": Policy(
        subject=Limit(calls=3, window=_HOUR),
        ceiling=Limit(calls=100, window=_HOUR),
        why=(
            "**The only limit here that is protecting money rather than CPU or "
            "reputation.** Every call hands a real charge to an SMS provider, and the "
            "number need not exist or belong to the caller, so the ceiling is the "
            "tightest of any policy in this table - a hundred texts an hour is already "
            "a bill worth noticing. Three per number matches the mail flows because the "
            "shape of the abuse is the same one, and because a text that did not arrive "
            "is the same problem as a mail that did not."
        ),
    ),
}


def longest_window() -> timedelta:
    """The longest window any policy uses.

    This is what the cold layer keeps a lapsed row for, so it is computed from the
    table rather than chosen. A hand-picked number here would be a second place the
    policies are written down, and the failure it would produce is the quiet kind: a
    row swept while its window was still live hands a caller a fresh budget in the
    middle of their window, which is precisely what the cold layer exists to prevent.
    """
    windows = [policy.ceiling.window for policy in POLICIES.values()]
    windows += [
        policy.subject.window for policy in POLICIES.values() if policy.subject is not None
    ]
    return max(windows)


def enforce(
    request: Request,
    bucket: str,
    subject: str | None,
    now: datetime | None = None,
) -> None:
    """Spend one call against ``bucket``, for this subject and the installation.

    Raises ``RateLimitedError`` if either dimension refuses. Returns ``None`` when
    both allow, because there is nothing useful to hand back - the caller wanted
    permission, not a report.

    ``subject`` is ``None`` when the request names nobody in particular. That is not an
    error and not a hole: the ceiling still applies, so such a request is bounded by
    the installation's budget even though no individual is charged for it.

    ``now`` is read **once** and handed to both checks, so the two cannot disagree
    about what time it is - which on a window boundary is the difference between a
    coherent answer and two contradictory ones from the same request.
    """
    policy = POLICIES[bucket]
    limiter = request.app.state.rate_limiter
    moment = datetime.now() if now is None else now

    if policy.subject is not None and subject is not None:
        verdict = limiter.check(bucket, subject, policy.subject, now=moment)
        if not verdict.allowed:
            raise RateLimitedError(verdict.retry_after_seconds)

    ceiling = limiter.check(bucket, INSTALLATION, policy.ceiling, now=moment)
    if not ceiling.allowed:
        raise RateLimitedError(ceiling.retry_after_seconds)
