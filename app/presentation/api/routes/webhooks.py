"""Where a payment provider tells this system what it did.

One route, and it is the only one in this API that is not addressed to a person.
That is not a detail of its implementation - it is the reason the file exists
separately from ``wallets``, and the reason the use case behind it
(``SettlePayment``) is not a method on ``WalletService``. Everything else here
begins by resolving a token into a ``User``; this begins by proving that a byte
string was signed by somebody holding a secret. **"Who are you" and "who sent
this" are different questions**, and an API that answered the second with the
first would be granting a privilege rather than checking a fact.

**The body is read as bytes and never as a model.** A signature is computed over
the bytes that arrived, so verifying one against anything re-serialized from them
verifies a *different* byte string - and a difference of a single space presents
as a wrong secret key, which sends whoever is debugging it to exactly the wrong
place. FastAPI parses a declared ``BaseModel`` parameter before the endpoint
runs, and there is no way to ask it for the raw body afterwards, so the raw body
is taken by an **async dependency** and parsed here by hand.

**This is the one async function in the presentation, and it is a dependency
rather than an endpoint.** ``app.py``'s rule is that nothing here is async and
nothing is decorated to look it, and the rule survives intact: ``request.body()``
is a coroutine because reading a socket is, the *endpoint* is still a plain
``def`` and still runs in the threadpool where its SQLite work belongs, and the
exception is one line in one file rather than a precedent. The alternative - an
``async def`` endpoint - would have put the settlement query on the event loop,
which is the mistake ``log_in_service``'s docstring warns about, made worse by
being on the endpoint a provider will hammer.

**Everything after the signature goes through one use case and comes back 200.**
What that means in practice: an unknown reference, a replay, an amount that
disagrees with the row, and an event this code has never heard of are all
*answers* rather than errors, because a provider that is told "no" will
eventually stop telling us anything. The two things that are not 200 are a
signature that does not verify (401 - stop, and do not retry) and a body that
cannot be read at all (400 - the same). See ``errors`` for both.
"""

import json
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Request

from app.application.payments.settle_payment import SettlePayment
from app.application.payments.settledPayment import SettlementOutcome
from app.domain.money.currency import Currency
from app.domain.money.exception import MoneyError
from app.domain.money.money import Money
from app.domain.payments.paymentProvider import PaymentProvider
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome
from app.presentation.api import schemas
from app.presentation.api.dependencies import payment_provider, settler_service
from app.presentation.api.errors import (
    InvalidWebhookSignatureError,
    MalformedWebhookError,
)

router = APIRouter(tags=["webhooks"])

logger = logging.getLogger(__name__)

#: The header Paystack signs with, spelled the way the wire spells it.
SIGNATURE_HEADER = "x-paystack-signature"

#: What this provider calls the four things this system knows how to settle.
#:
#: **The translation lives here rather than in the domain, and that is the whole
#: arrangement.** ``ProviderEvent``'s members are *our* names - ``charge_succeeded``
#: rather than ``charge.success`` - so that no aggregate, use case or test has to
#: spell a vendor's dot-separated string. A second provider would add a second
#: table beside this one and change nothing below it, which is the test of
#: whether a seam is in the right place.
#:
#: An event that is not in this table is **not an error**. Providers add events,
#: and one arriving that this deployment does not act on is a normal Tuesday - so
#: it is acknowledged and reported, never refused. Refusing would make Paystack
#: retry an event that will be equally uninteresting every time.
EVENTS = {
    "charge.success": ProviderEvent.CHARGE_SUCCEEDED,
    "transfer.success": ProviderEvent.TRANSFER_SUCCEEDED,
    "transfer.failed": ProviderEvent.TRANSFER_FAILED,
    "transfer.reversed": ProviderEvent.TRANSFER_REVERSED,
}

#: How many of the provider's subunits make one unit - kobo to the naira.
#:
#: The inverse of the factor ``PaystackPaymentProvider._subunit`` applies, and it
#: lives with the other Paystack wire facts for the same reason the event names
#: do: "amounts arrive as an integer number of kobo" is a statement about
#: *Paystack*, not about money. ``Money`` knows nothing about it and must not.
SUBUNITS_PER_UNIT = 100


async def raw_body(request: Request) -> bytes:
    """The request body, exactly as it arrived, without being parsed.

    See the module docstring for why this is a dependency rather than something
    the endpoint does, and for why it is the one async thing in this
    presentation. Both halves are worth repeating at the definition, because
    this is the line somebody will try to "simplify" into a Pydantic model.

    A body of any size is read - there is no cap here - which is acceptable for
    exactly one reason: the signature is checked before the body is believed, so
    an attacker cannot make this endpoint do anything with a large payload
    except hash it. The limit that matters is at the reverse proxy, where it
    belongs.
    """
    return await request.body()


@router.post("/webhooks/paystack", response_model=schemas.WebhookAck)
def receive_paystack_event(
    body: bytes = Depends(raw_body),
    signature: str | None = Header(default=None, alias=SIGNATURE_HEADER),
    provider: PaymentProvider = Depends(payment_provider),
    settler: SettlePayment = Depends(settler_service),
) -> schemas.WebhookAck:
    """Settle whatever a provider says happened, or say why nothing was settled.

    **The signature is checked before the body is looked at**, which is an
    ordering rather than a style. Everything below this line reads a third
    party's JSON and, on the happy path, moves money; none of it may run on a
    body whose author is unknown. Had the parse come first, a malformed payload
    from a stranger would be reported back to that stranger in this API's error
    vocabulary - a small thing to leak, and a free oracle for anybody probing.

    **One refusal for three failures.** An absent header, a header that is not
    this installation's signature, and a body altered after being signed are all
    ``InvalidWebhookSignatureError`` with the same body, because they are one
    fact: this request cannot be shown to have come from the provider. Which of
    the three it was is a distinction that exists in the server's log and
    nowhere in the response - telling a stranger *how* their forgery failed is
    the one piece of help they should not get. ``verify_signature`` takes
    ``str | None`` for the same reason: "absent" and "wrong" are the same answer,
    so making the caller branch on the difference would be a branch with no
    consumer.

    **That sentence about the log was not true when it was written, and it is
    now.** There was no server log: this module and every other in ``app/``
    imported no ``logging`` at all, so a forged webhook, a replayed one and an
    unknown reference left exactly the same trace - none. The line below is what
    the paragraph above always promised, and it is deliberately the narrowest one
    that keeps the promise: **whether a signature was presented at all**, and
    never the body, never the header's value, and never a comparison of the two.
    "Absent" and "present but not this installation's" is the whole of what is
    knowable, and calling them one thing in the log would put an operator back
    where they started.

    **The provider is a dependency, so an unconfigured install never gets here.**
    ``dependencies.payment_provider`` raises a 503 when there is no adapter -
    which must never be a 401, because a 401 tells Paystack to stop sending and
    the money taken in the meantime would be settled by nothing. See
    ``PaymentsUnconfiguredError``.

    What remains, in order: read the JSON, find the event name, and if it is one
    this system settles, hand a ``ProviderOutcome`` to ``SettlePayment``. Every
    one of those outcomes is a 200 - including the ones where nothing moved -
    and the body says which it was.
    """
    if not provider.verify_signature(body, signature):
        logger.warning(
            "refused a webhook whose signature %s",
            "was not presented at all" if signature is None else "did not match",
        )
        raise InvalidWebhookSignatureError(
            "the signature does not match this installation's payment provider"
        )

    payload = _json_object(body)
    event_name = _event_name(payload)
    event = EVENTS.get(event_name)

    if event is None:
        # Acknowledged, and deliberately without parsing the rest: an event this
        # deployment does not act on has no fields worth validating, and
        # refusing one for a missing reference would be this API inventing a
        # requirement for a message it is going to ignore anyway.
        logger.info("ignored a webhook event: %s", event_name)
        return schemas.WebhookAck(
            outcome=SettlementOutcome.EVENT_IGNORED.value,
            detail="this installation does not settle this kind of event",
        )

    settled = settler.settle(_outcome_from(event, payload))
    # One line per signed event that reached settlement, carrying the outcome and
    # the reference. **The reference is the whole value of the line**: it is the
    # join between this ledger and Paystack's dashboard, and it is the only field
    # that lets somebody holding a payment in front of them find out what this
    # system did with it. The outcome is here because ``AMOUNT_DISAGREES`` and
    # ``UNKNOWN_REFERENCE`` are money that arrived and was not credited, and a
    # log an operator has to reconstruct that from is a log that gets ignored.
    logger.info(
        "a webhook settled %s: %s",
        settled.reference or "(no reference)",
        settled.outcome.value,
    )
    return schemas.WebhookAck(
        outcome=settled.outcome.value,
        reference=settled.reference,
        detail=settled.detail,
    )


# --- reading the wire -------------------------------------------------------


def _json_object(body: bytes) -> dict:
    """The body as a JSON object, or ``MalformedWebhookError``.

    ``json`` rather than a Pydantic model, and the reason is at the top of this
    module: the body had to stay bytes for the signature, so there is nothing
    left for a model to be declared against. Parsing by hand means every failure
    below is one this file chose, rather than whatever shape a library's
    validation error happens to take this month.

    A body that parses to a list or a string is refused here as firmly as one
    that does not parse at all. Both are "not an event", and the difference
    between them is not something a caller could act on.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        raise MalformedWebhookError("the request body is not JSON") from None

    if not isinstance(payload, dict):
        raise MalformedWebhookError("the request body is not a JSON object")
    return payload


def _event_name(payload: dict) -> str:
    """The provider's own name for what happened, or a refusal.

    Returned as the raw string rather than resolved to a ``ProviderEvent``,
    because the caller's next move is a table lookup that is allowed to miss -
    and a function that raised on an unknown name would make the ordinary case
    (a new event type) an error path.
    """
    name = payload.get("event")
    if not isinstance(name, str) or not name:
        raise MalformedWebhookError("the event has no name")
    return name


def _outcome_from(event: ProviderEvent, payload: dict) -> ProviderOutcome:
    """One settled fact, read out of the event that claims it.

    **Every field this reaches for is one the provider documents for every event
    of that kind**, so a missing one is a bug or a forgery rather than a variant
    - and the signature was checked upstream, which is what makes "forgery" the
    unlikely of the two. Either way the answer is the same 400: this body cannot
    be read, and retrying it will not change that.

    ``reference`` is the field the whole feature turns on. It is what
    ``SettlePayment`` looks a ledger row up by, and it is the value this system
    chose and handed to the provider at initiation - so a webhook cannot name a
    movement that was never asked for. A reference that matches no row settles
    nothing; that is a *later* refusal, and a 200, because the reference was
    legible even though it was unknown.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MalformedWebhookError("the event carries no data")

    reference = data.get("reference")
    if not isinstance(reference, str) or not reference:
        raise MalformedWebhookError("the event names no reference")

    amount = data.get("amount")
    # ``bool`` is an ``int`` in Python, and ``isinstance(True, int)`` is ``True``
    # - so the obvious check would accept ``"amount": true`` as one kobo. The
    # explicit exclusion is the same one ``Money`` makes at its own centre, for
    # the same reason: a boolean is never an amount.
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise MalformedWebhookError("the event carries no integer amount")

    currency = data.get("currency")
    if not isinstance(currency, str):
        raise MalformedWebhookError("the event names no currency")

    # Three failures, one refusal, and the third is why the ``try`` reaches past
    # the ``Money``: ``Currency`` is a plain enum, so a name it has never heard of
    # raises a built-in ``ValueError`` rather than a domain error - and a
    # ``ValueError`` escaping here would be a 500, which is the one answer this
    # endpoint must never give a body it cannot read. The same argument covers
    # ``ProviderOutcome`` refusing an amount of zero: a signed event claiming
    # nothing arrived is a body that cannot be read as one.
    #
    # The domain's own sentence is kept as the detail rather than replaced with a
    # generic one, because it names which of the failures it was and the detail
    # field is read by a human looking at a log, not by the provider.
    try:
        money = Money(Decimal(amount) / SUBUNITS_PER_UNIT, Currency(currency))
        return ProviderOutcome(event=event, reference=reference, amount=money)
    except (MoneyError, ValueError) as refused:
        raise MalformedWebhookError(str(refused)) from None
