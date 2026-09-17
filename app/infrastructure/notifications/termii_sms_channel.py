"""Termii: the one adapter that puts a text message on the wire.

The second outbound adapter in this codebase, and the first one where a *success
response* cannot be trusted to mean the message was sent. That is the whole of
what is interesting here, and it is worth stating before the code because it
decides the shape of ``send``:

**Termii answers a refused message with an HTTP 200.** The refusal is in the
body, as ``{"code": "...", "message": "..."}``, and a message that was accepted
carries ``"code": "ok"``. So a channel that read the status code and stopped
would report success for a text nobody will receive - and for a one-time code
that is the worst failure this slice can have, because the person waits for
something that is not coming and the system believes it arrived. This adapter
therefore reads the body on every call, including the ones that look fine, and
``ACCEPTED_CODE`` is the only thing that means "sent".

**``httpx`` is already a dependency and this adds nothing to it.** The same
library the Paystack adapter uses, for the same reason and with the same shape of
failure handling - a transport that was good enough for money is good enough for
a text message.

**One method and no ``_request``**, unlike ``PaystackPaymentProvider``. That
adapter has two outbound methods sharing a transport, so the bearer token, the
timeout and the failure handling have one home there. There is one call here and
nothing to share it with, so the call is written out where it happens; extracting
a transport for a single caller would be a seam with nothing on the other side of
it.
"""

import httpx

from app.domain.notifications.exception import SmsProviderError
from app.domain.notifications.smsChannel import SmsChannel
from app.domain.notifications.smsMessage import SmsMessage

#: Where Termii lives.
#:
#: A module constant rather than a setting, for the reason
#: ``paystack_payment_provider.BASE_URL`` gives: there is no second value it could
#: sensibly take in production, so a configurable base URL would be a setting
#: whose only non-default values are a mistake or a mock - and a mock is what the
#: port's injection seam is for.
#:
#: **Unlike Paystack's, this one has not been confirmed by a live call.** It is
#: the host Termii's documentation gives, and the README records the live run as
#: owed for this slice. Saying so here rather than leaving it implied is the point
#: of the paystack module's own note about ``INVALID_PAYER_EMAIL_CODE`` having been
#: *learned* from a live call: the difference between a fact a run established and
#: a fact documentation supplied is the difference between something already
#: proven and something still to prove.
BASE_URL = "https://api.ng.termii.com"

#: The one endpoint this integration calls.
SMS_ENDPOINT = "/api/sms/send"

#: How long a send may take, in seconds.
#:
#: **Not optional, and not a tuning knob** - the argument
#: ``SmtpNotificationChannel.DEFAULT_TIMEOUT`` makes and
#: ``PaystackPaymentProvider`` repeats. Without one, a provider that accepts the
#: connection and then says nothing leaves the request blocked until the operating
#: system's TCP timeout, which is measured in minutes and cannot be interrupted by
#: this process. Fifteen rather than the mail channel's ten, matching the payment
#: adapter, because this is a request/response API rather than a submission
#: dialogue.
DEFAULT_TIMEOUT = 15

#: The characters a recipient may be written in, as the SMS API accepts them.
#:
#: Digits only, in international form, with **no leading ``+``** and **no national
#: trunk ``0``** - which is exactly the shape ``phoneNumber.fold_phone`` produces,
#: so the two agree by construction rather than by luck. The trunk prefix is the
#: one worth naming: ``08012345678`` and ``+2348012345678`` are the same handset,
#: and which spelling a provider's ``to`` field wants is the kind of detail that
#: works in a test and fails in production.
#:
#: **A second copy of a rule that already exists elsewhere, written out on
#: purpose.** It could be imported from ``app.domain.identity.phoneNumber``, and
#: importing it would make this agree with the fold by construction rather than by
#: being right - which is precisely what went wrong the last time. The deposit
#: route sent Paystack a reference containing a character Paystack refuses, and
#: 2,042 passing tests could not see it because every copy of the rule involved
#: had been derived from the others. A copy taken from the provider's own
#: documentation can disagree with the producer, and disagreeing is what makes it
#: worth having.
RECIPIENT_ALPHABET = frozenset("0123456789")

#: The longest sender id Termii accepts, in characters.
#:
#: Length is the only part of "is this sender id usable" that is knowable from
#: here. The rest is an account-level fact - a sender id must be registered and
#: approved before the account may send under it - and no code in this repository
#: can check it, which is why a wrong sender id that is the right length fails as
#: a provider refusal and is reported as one.
MAX_SENDER_ID_LENGTH = 11

#: The one message type and channel this integration sends.
#:
#: Two separate fields in Termii's request, and both are sent explicitly rather
#: than left to whatever the far end defaults to. ``plain`` is a text with no
#: template placeholders; ``generic`` is the route that reaches any network. Named
#: constants so that changing either is a deliberate line rather than a value that
#: happened to arrive - the same argument ``PaystackPaymentProvider`` makes for
#: ``SUPPORTED_CURRENCY``.
MESSAGE_TYPE = "plain"

#: See ``MESSAGE_TYPE``.
MESSAGE_CHANNEL = "generic"

#: Termii's word for "accepted", and the only thing in a response that means sent.
#:
#: Read from the body rather than from the status, for the reason this module's
#: docstring gives: a refusal arrives with a 200. There is one accepted value
#: rather than a set, and every other value - including a body with no ``code`` at
#: all - is a refusal, which is the safe direction for the one call in this system
#: whose failure mode is a person waiting for a code that never arrives.
ACCEPTED_CODE = "ok"


class TermiiSmsChannel(SmsChannel):
    """The Termii implementation of the SMS port.

    Raises ``SmsProviderError`` on every failure rather than returning a status,
    because that is the contract the port states: the caller records the exception
    or refuses the request, so the exception *is* the error report.

    **The provider's ``message_id`` is thrown away**, and that is a decision rather
    than an omission. Termii returns one, and carrying it back would mean widening
    the port's return type to something a delivery receipt feature would eventually
    want - but nothing today would read it, and a value that is returned and never
    used is a claim that delivery is being tracked. When receipts are built they
    will need a column to store an id against, and that is a conversation for the
    day there is one.

    Holds a key and a sender id and nothing else: no client, no connection, no
    session. Each send opens what it needs and closes it again, the choice
    ``SmtpNotificationChannel`` and ``PaystackPaymentProvider`` both make and for
    the same reason - a long-lived connection is state that outlives the request,
    and state that outlives the request is what has to be thought about at
    shutdown.
    """

    def __init__(
        self,
        api_key: str,
        sender_id: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._api_key = api_key
        self._sender_id = sender_id
        self._timeout = timeout

    def send(self, message: SmsMessage) -> None:
        """Hand one text to Termii, or raise.

        **The recipient is refused here when it is not in the shape Termii
        accepts, and this is the one guard of its kind in this codebase whose
        reason is not tidiness.** Everywhere else a malformed value costs a
        refused call - Paystack is asked and says no, and the answer is a
        sentence. A text message is not like that. An address that is nearly right
        may still deliver, and it delivers to whoever holds it, so a value that
        did not come out of ``fold_phone`` is a message this system will not send.
        A phone number is also the smallest enumerable identifier in this product,
        which makes "send it and see" a worse habit here than it is anywhere else.

        **The guard is a courtesy and Termii remains the authority**, exactly as
        ``refuse_unusable_payer_email`` is a courtesy and Paystack is the
        authority for an address. A recipient the provider refuses for a reason
        this codebase did not anticipate - a number that cannot be routed, a
        network that will not accept the sender id - arrives as the same
        ``SmsProviderError`` with the provider's own words attached, so a caller
        cannot tell the two apart and nothing rests on this reading of the
        contract. See ``_refuse_unusable_recipient``.

        **The sender id is checked for length and for nothing else**, and the
        comment on ``MAX_SENDER_ID_LENGTH`` carries why: everything else about a
        sender id is a fact about the account.

        ``api_key`` is never looked at here. It goes into the body because that is
        where Termii's API takes it - not as a bearer token, which is the one
        visible difference from the Paystack adapter - and a wrong key therefore
        arrives as a body refusal naming the key rather than as a 401.
        """
        _refuse_unusable_recipient(message.recipient)

        if len(self._sender_id) > MAX_SENDER_ID_LENGTH:
            raise SmsProviderError(
                f"the sender id is longer than {MAX_SENDER_ID_LENGTH} characters, "
                "which is the longest the SMS provider accepts"
            )

        try:
            response = httpx.post(
                f"{BASE_URL}{SMS_ENDPOINT}",
                json={
                    "api_key": self._api_key,
                    "to": message.recipient,
                    "from": self._sender_id,
                    "sms": message.body,
                    "type": MESSAGE_TYPE,
                    "channel": MESSAGE_CHANNEL,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as failure:
            # ``httpx``'s own exception does not escape, for the reason the
            # payment adapter gives: a caller that had to catch
            # ``httpx.ConnectError`` would be a caller that knows which library
            # this adapter uses, which is the thing the port exists to prevent.
            raise SmsProviderError(
                f"could not reach the SMS provider: {type(failure).__name__}"
            ) from failure

        # Read quietly before the status is judged, so that a 5xx carrying an HTML
        # error page produces a sentence about the status rather than a JSON
        # decode error - the ordering ``PaystackPaymentProvider._request`` uses,
        # for the same reason. A diagnostic that can itself raise is worse than no
        # diagnostic.
        body = _json_or_none(response)

        if response.status_code >= 400:
            # Written out here rather than through a named helper, and the
            # difference from the payment adapter is that this is the *only* place
            # this module turns a refusal into words. There, ``_refusal_sentence``
            # exists because two raise sites had to agree; a helper for one caller
            # would be a second name for the same line.
            raise SmsProviderError(
                f"the SMS provider refused the message with "
                f"{response.status_code}{_provider_words(body)}"
            )

        if body is None:
            # A 2xx whose body is not JSON. Reached on a proxy in the way, and it
            # cannot be treated as success: the whole point of this adapter is that
            # the body says whether the message was accepted, so a body that cannot
            # be read is a send whose fate is unknown - and an unknown fate is
            # reported as a failure, because the alternative is a one-time code
            # that this system believes arrived.
            raise SmsProviderError(
                "the SMS provider returned a body that is not JSON, so it cannot "
                "be told whether the message was accepted"
            )

        if _code_in(body) != ACCEPTED_CODE:
            raise SmsProviderError(
                "the SMS provider did not accept the message"
                + _provider_words(body)
            )


def _refuse_unusable_recipient(recipient: str) -> None:
    """Refuse a destination that is not in the shape the SMS API accepts.

    Four shapes, each of which is a different bug upstream: an empty value, a
    leading ``+`` that means the number was never folded, a leading ``0`` that
    means the national trunk prefix survived the fold, and anything outside the
    digits that means what reached this channel was never a phone number. All four
    are a fold that did not run or a caller that skipped it - no number a person
    typed can arrive here in these shapes, because typing goes through
    ``checked_phone`` first.

    **The value is not quoted back**, unlike ``PayerEmailRefusedError``'s sentence,
    and the difference is not an inconsistency. That message quotes an address the
    *caller typed*, so telling them about it tells them about their own input.
    This one is reachable only through an internal bug - the value came out of
    this system's own store rather than out of a request - so a sentence carrying
    it would put somebody's phone number into an error body for nothing the caller
    could act on. Which shape was wrong is the whole of the actionable part.

    An empty value is refused first rather than left to the alphabet test, because
    it *passes* that test: the empty set is a subset of everything, so a check
    written as "every character is a digit" is vacuously true of a string with no
    characters. That is the same trap that made ``fold_phone`` return a number for
    an input with no digits in it, and it is written down here for the same reason.
    """
    if not recipient:
        raise SmsProviderError(
            "the recipient number is empty, and there is no one to send a text to"
        )

    if recipient.startswith("+"):
        raise SmsProviderError(
            "the recipient number is not in international format: it begins with "
            "'+', which the SMS API does not accept"
        )

    if recipient.startswith("0"):
        # No country code begins with zero - that is exactly what a national trunk
        # prefix is for - so a leading zero is never the start of a country code
        # that the fold failed to strip.
        raise SmsProviderError(
            "the recipient number still carries a national trunk prefix: it "
            "begins with '0', and no country code does"
        )

    if not set(recipient) <= RECIPIENT_ALPHABET:
        raise SmsProviderError(
            "the recipient number contains characters that are not digits"
        )


def _json_or_none(response: httpx.Response) -> object:
    """The response's JSON body, or ``None`` when there is not a usable one.

    ``PaystackPaymentProvider``'s helper of the same name, and quiet for the same
    reason: this runs on paths that are already refusing or already suspicious, so
    a body that is absent, HTML, or not an object all come back as ``None`` and
    the readers below contribute nothing rather than raising.
    """
    try:
        return response.json()
    except ValueError:
        return None


def _code_in(body: object) -> str | None:
    """The provider's ``code``, when the body is an object carrying a string one.

    The field this adapter is built around, and the only part of Termii's response
    it matches on. A body that is absent, a list, or an object whose ``code`` is
    not a string contributes ``None`` - which is never ``ACCEPTED_CODE``, so an
    unreadable answer is a refusal rather than a success by default.
    """
    if not isinstance(body, dict):
        return None

    code = body.get("code")
    return code if isinstance(code, str) else None


def _provider_words(body: object) -> str:
    """The provider's own ``message`` and ``code``, as a suffix, or nothing at all.

    ``PaystackPaymentProvider``'s helper one provider over, and it earns its place
    the same way that one did: the deposit bug that cost two round trips to a bare
    ``curl`` was answered with a provider sentence this system could have repeated
    and did not. Two named fields of a documented error contract, extracted rather
    than quoted - a response body is a third party's text and may quote the request
    back, which for this adapter means the ``api_key`` it was sent.

    Quiet about anything it does not recognise, so a diagnostic cannot itself fail
    on the path that exists to report a failure.
    """
    if not isinstance(body, dict):
        return ""

    message = body.get("message")
    if not isinstance(message, str):
        return ""

    code = body.get("code")
    return f": {message}" + (f" ({code})" if isinstance(code, str) else "")
