"""The Termii adapter: one call out, and one field of the answer believed.

One half, tested against a recording double the way ``SmtpNotificationChannel`` and
``PaystackPaymentProvider``'s outbound half are - what matters is not that HTTP
works, but that the right URL, the right fields and the right *number* of calls go
out, and that the answer is read correctly coming back.

**The answer is where this file earns its keep.** Termii reports a refused message
with an HTTP 200 and puts the refusal in the body, so the status code is not the
thing under test here and a suite that only ever scripted ``200`` and checked for
no exception would pass on an adapter that believed every response. The bodies
below are the two sides of that: one carrying ``"code": "ok"`` and one carrying a
refusal, both with a 200.

The card the terminal half plays is the same one the deposit tests play: a
malformed recipient must be refused **before anything leaves the process**, and
that is asserted by there being no recorded call at all rather than by the
exception's type. Sending it and reading the refusal would be a message paid for
on a route nobody can use.

No test here reaches the network. There is no skip marker and no test that only
runs when a key is set - the injection seam is what makes that unnecessary.
"""

import httpx
import pytest

from app.domain.notifications.exception import SmsProviderError
from app.infrastructure.notifications import termii_sms_channel
from app.infrastructure.notifications.termii_sms_channel import (
    ACCEPTED_CODE,
    BASE_URL,
    DEFAULT_TIMEOUT,
    MAX_SENDER_ID_LENGTH,
    MESSAGE_CHANNEL,
    MESSAGE_TYPE,
    SMS_ENDPOINT,
    TermiiSmsChannel,
)
from tests.conftest import TEST_TERMII_API_KEY, TEST_TERMII_SENDER_ID, FakeSmsChannel

#: A number in the shape ``fold_phone`` produces, which is the only shape this
#: adapter will send to. Nigerian because that is what ``DEFAULT_COUNTRY_CODE``
#: makes of a national number, so it is the value this system actually produces.
A_FOLDED_NUMBER = "2348012345678"

#: The trunk prefix a fold is supposed to remove, kept here so the test that
#: refuses it reads as the fold having failed rather than as a typo.
A_NATIONAL_SPELLING = "08012345678"


class AText:
    """The smallest thing the port accepts: two fields and nothing else.

    Deliberately not an aggregate from the domain. What is under test is the wire,
    and the port's own claim is that the shape is what qualifies a record for a
    channel - so a test that had to build a ``Notification`` to send a text would
    be testing the port's flexibility nowhere at all.
    """

    def __init__(self, recipient: str = A_FOLDED_NUMBER, body: str = "Your code is 123456"):
        self.recipient = recipient
        self.body = body


def an_acceptance(**overrides) -> dict:
    """What Termii answers a message it took, in its own field names.

    Trimmed rather than invented: ``code``, ``message``, ``message_id`` and
    ``balance`` are the four fields a real acceptance carries, and the adapter
    reads exactly one of them. Keeping the other three here is what would let a
    typo in that one field show up as a failure rather than as a lucky pass.
    """
    body = {
        "code": ACCEPTED_CODE,
        "message": "Successfully Sent",
        "message_id": "termii_msg_1",
        "balance": 12,
    }
    body.update(overrides)
    return body


def a_refusal(code: str = "invalid_sender_id", message: str = "Invalid Sender ID") -> dict:
    """What Termii answers a message it would not send, with a 200 in front of it.

    A builder rather than a constant because these tests are about *codes* - the
    one field the adapter matches on - and the only thing that changes between
    them is the word.
    """
    return {"code": code, "message": message}


class RecordingPost:
    """Stands in for ``httpx.post``, and writes down what it was asked to send.

    One instance per test. ``raises`` is an exception to raise instead of
    answering, which is how an unreachable provider is expressed - the same shape
    ``RecordingRequest`` and ``RecordingSmtp`` use.

    The signature is ``(url, **kwargs)`` rather than the real one, so that a change
    to how the adapter calls this - a new keyword, a different one - shows up in
    the recorded call rather than in a ``TypeError``. What is being asserted about
    is the content of the call.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.response = httpx.Response(200, json=an_acceptance())
        self.raises: Exception | None = None

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return self.response

    @property
    def call(self) -> dict:
        """The one call made, asserted to be exactly one.

        A test that asked for ``calls[0]`` would pass on an adapter that sent
        twice, and sending twice is the same one-time code texted twice - billed
        twice, and a second message arriving after the first has been spent.
        """
        assert len(self.calls) == 1, self.calls
        return self.calls[0]


@pytest.fixture
def posting(monkeypatch):
    """Replace ``httpx.post`` for the duration of one test.

    ``httpx.post`` rather than ``httpx.request``, because that is the seam this
    adapter calls through. It has one outbound method and no shared transport, so
    there is one function to replace - see the module docstring for why there is
    no ``_request`` here.
    """
    recorder = RecordingPost()
    monkeypatch.setattr(termii_sms_channel.httpx, "post", recorder)
    return recorder


@pytest.fixture
def channel():
    return build_channel()


def build_channel(**overrides) -> TermiiSmsChannel:
    kwargs = dict(
        api_key=TEST_TERMII_API_KEY,
        sender_id=TEST_TERMII_SENDER_ID,
    )
    kwargs.update(overrides)
    return TermiiSmsChannel(**kwargs)


class TestWhatGoesOnTheWire:
    def test_it_posts_to_the_send_endpoint(self, channel, posting):
        channel.send(AText())

        assert posting.call["url"] == f"{BASE_URL}{SMS_ENDPOINT}"

    def test_the_whole_payload_is_asserted(self, channel, posting):
        """**Asserted as the whole body, because what matters is the absence.**

        Six fields go out and no others: the key, the destination, the sender, the
        words, and the two that say how to route it. A test that checked the
        recipient alone would keep passing on the day somebody added a field
        carrying something else about this system or this person - and the one
        place this product hands a fact about somebody to a third party is the
        place to notice that.
        """
        channel.send(AText(recipient=A_FOLDED_NUMBER, body="Your code is 123456"))

        assert posting.call["json"] == {
            "api_key": TEST_TERMII_API_KEY,
            "to": A_FOLDED_NUMBER,
            "from": TEST_TERMII_SENDER_ID,
            "sms": "Your code is 123456",
            "type": MESSAGE_TYPE,
            "channel": MESSAGE_CHANNEL,
        }

    def test_the_destination_comes_from_the_message_not_the_channel(
        self, posting
    ):
        """The recipient is a fact about the message, which is what makes one
        channel serve a signup code and a reset code without knowing which."""
        build_channel().send(AText(recipient="2348099999999"))

        assert posting.call["json"]["to"] == "2348099999999"

    def test_the_sender_and_the_words_come_from_the_channel_and_the_message(
        self, posting
    ):
        build_channel(sender_id="OtherName").send(AText(body="hello"))

        assert posting.call["json"]["from"] == "OtherName"
        assert posting.call["json"]["sms"] == "hello"

    def test_it_sets_a_timeout(self, channel, posting):
        """Without one, a provider that accepts the connection and says nothing
        leaves the request blocked until the operating system gives up."""
        channel.send(AText())

        assert posting.call["timeout"] == DEFAULT_TIMEOUT

    def test_it_sends_once(self, channel, posting):
        channel.send(AText())

        assert len(posting.calls) == 1


class TestTheRecipient:
    """Four shapes refused, and none of them reaches the network.

    **The assertion in each of these is ``posting.calls == []``**, and that is the
    substance rather than a detail. Sending a malformed destination and reporting
    the provider's refusal would still text somebody's phone - the whole reason
    this guard exists is that "nearly right" is the dangerous case for a message,
    not the harmless one.
    """

    @pytest.mark.parametrize(
        "recipient",
        [
            f"+{A_FOLDED_NUMBER}",
            "002348012345678",
            "234 801 234 5678",
            "234-801-234-5678",
            "",
        ],
        ids=["a-plus", "a-trunk-meets-international", "spaces", "dashes", "empty"],
    )
    def test_a_recipient_outside_the_alphabet_is_refused_without_calling_out(
        self, channel, posting, recipient
    ):
        with pytest.raises(SmsProviderError):
            channel.send(AText(recipient=recipient))

        assert posting.calls == []

    def test_a_national_spelling_is_refused_and_names_the_trunk(self, channel, posting):
        """**The one a fold regression produces.**

        ``08012345678`` is what somebody types and what ``fold_phone`` exists to
        turn into ``2348012345678``. If the fold ever stops doing that, every text
        this system sends becomes a provider refusal - and the bug would live in a
        domain module while the symptom appeared here, so the sentence names the
        cause rather than the symptom.
        """
        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText(recipient=A_NATIONAL_SPELLING))

        assert "trunk" in str(refused.value)
        assert posting.calls == []

    def test_a_plus_prefixed_number_is_refused_and_names_the_format(
        self, channel, posting
    ):
        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText(recipient=f"+{A_FOLDED_NUMBER}"))

        assert "international format" in str(refused.value)
        assert posting.calls == []

    def test_the_sentence_does_not_carry_the_number(self, channel, posting):
        """**A deliberate refusal to echo the value.**

        The number in this message came out of this system's own store rather than
        out of a request, so a sentence quoting it would put somebody's phone
        number into an error body for nothing the caller could act on. Which shape
        was wrong is the actionable half. Compare ``PayerEmailRefusedError``, whose
        sentence *does* quote the address - because there the address is what the
        caller typed.
        """
        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText(recipient="not-a-number"))

        assert "not-a-number" not in str(refused.value)

    def test_a_correctly_folded_number_is_sent(self, channel, posting):
        """The boundary in the other direction: the guard is not a filter that
        refuses everything, which a test suite of only refusals could not tell."""
        channel.send(AText(recipient=A_FOLDED_NUMBER))

        assert posting.call["json"]["to"] == A_FOLDED_NUMBER


class TestTheSenderID:
    def test_an_over_long_sender_id_is_refused_without_calling_out(self, posting):
        """Length is the one part of "is this sender id usable" that is knowable
        here; whether it is registered on the account is not, and Termii's refusal
        is the only honest answer to that."""
        channel = build_channel(sender_id="x" * (MAX_SENDER_ID_LENGTH + 1))

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert str(MAX_SENDER_ID_LENGTH) in str(refused.value)
        assert posting.calls == []

    def test_a_sender_id_at_the_ceiling_is_sent(self, channel, posting):
        """The boundary, asserted inclusively: an off-by-one here would refuse an
        eleven-character sender id, which is a legal one."""
        build_channel(sender_id="x" * MAX_SENDER_ID_LENGTH).send(AText())

        assert posting.call["json"]["from"] == "x" * MAX_SENDER_ID_LENGTH


class TestTheBodyIsWhatSaysSent:
    """The half that makes this adapter different from the payment one.

    A refusal arrives with a 200, so every test here scripts a successful status
    and an unsuccessful body. An adapter that read only ``response.status_code``
    would pass all four of them without raising, which is why the assertion is the
    exception rather than the absence of one.
    """

    def test_a_body_whose_code_is_not_ok_is_refused(self, channel, posting):
        posting.response = httpx.Response(200, json=a_refusal())

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert "did not accept the message" in str(refused.value)

    def test_the_providers_own_words_come_through(self, channel, posting):
        """**The lesson the deposit bug bought, applied to a second provider.**

        A refusal reported as "the provider refused the call" cost two round trips
        to a bare ``curl`` once already. The body here is a refusal Termii would
        send for an unregistered sender id, and the assertion is simply that this
        system repeats it.
        """
        posting.response = httpx.Response(
            200,
            json=a_refusal(
                code="invalid_sender_id",
                message="Sender ID is not registered on this account",
            ),
        )

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert "Sender ID is not registered on this account" in str(refused.value)
        assert "invalid_sender_id" in str(refused.value)

    def test_a_body_with_no_code_at_all_is_refused(self, channel, posting):
        """An unreadable answer is never a success by default.

        ``_code_in`` answers ``None`` for a body that is not an object, is an
        object whose ``code`` is not a string, or has no ``code`` - and ``None``
        is not ``ok``, so all three land here. The direction is the safe one: for
        a one-time code, treating an unknown answer as sent means a person waits
        for something that is not coming.
        """
        posting.response = httpx.Response(200, json={"message": "Something"})

        with pytest.raises(SmsProviderError):
            channel.send(AText())

    @pytest.mark.parametrize(
        "body",
        [["not", "an", "object"], {"code": 42, "message": 7}],
        ids=["a-list", "an-object-of-the-wrong-types"],
    )
    def test_a_body_that_is_not_an_object_is_refused_quietly(
        self, channel, posting, body
    ):
        """A shape nobody expected contributes no words, and must not raise
        anything but the domain's own refusal - a diagnostic that can itself fail,
        on a path whose purpose is to report a failure, is worse than none."""
        posting.response = httpx.Response(200, json=body)

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert str(refused.value) == (
            "the SMS provider did not accept the message"
        )

    def test_a_body_that_is_not_json_is_refused(self, channel, posting):
        """A 200 carrying an HTML error page, which is what a proxy in the way
        produces - and it cannot be read as acceptance."""
        posting.response = httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert "not JSON" in str(refused.value)

    def test_an_acceptance_is_not_an_error(self, channel, posting):
        """The other direction, so a suite of refusals cannot be satisfied by an
        adapter that raises on everything."""
        posting.response = httpx.Response(200, json=an_acceptance())

        channel.send(AText())


class TestWhenTheProviderSaysNo:
    def test_a_refused_status_is_reported_with_the_provider_s_words(
        self, channel, posting
    ):
        """A non-2xx is the other way a refusal arrives - a wrong key, a malformed
        request - and it carries the same two error-contract fields."""
        posting.response = httpx.Response(
            400,
            json=a_refusal(code="invalid_api_key", message="Invalid API key"),
        )

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert "400" in str(refused.value)
        assert "Invalid API key" in str(refused.value)
        assert "invalid_api_key" in str(refused.value)

    def test_a_status_with_no_provider_words_still_says_the_status(
        self, channel, posting
    ):
        """A 500 with an HTML error page - the status is what a human acts on."""
        posting.response = httpx.Response(500, text="<html>oops</html>")

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert "500" in str(refused.value)

    def test_an_unreachable_provider_is_a_provider_error(self, channel, posting):
        """**``httpx``'s exception does not escape.**

        A caller that had to catch ``httpx.ConnectError`` would be a caller that
        knows which library this adapter uses, which is the thing the port exists
        to prevent - and a caller has one thing to catch.
        """
        posting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(SmsProviderError) as refused:
            channel.send(AText())

        assert "ConnectError" in str(refused.value)

    def test_it_does_not_retry(self, channel, posting):
        """**A retry would be one the caller did not ask for and cannot see.**

        Sending a second time is not idempotent here the way a payment reference
        makes one: it is a second text to a real handset, billed a second time.
        When a retry is the right answer it belongs one level up, where somebody
        can decide whether trying again is worth another message.
        """
        posting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(SmsProviderError):
            channel.send(AText())

        assert len(posting.calls) == 1


class TestTheFakeTheSuiteSendsThrough:
    """The other statement of the same rule, tested against itself.

    The rule "a recipient is international digits" now exists in three places: it
    is *produced* by ``phoneNumber.fold_phone``, *guarded* by the adapter above,
    and *asserted* by ``FakeSmsChannel``, which every application and API test
    sends through. The three are written independently on purpose - see the fake's
    docstring for why importing one from another is the mistake this is arranged to
    avoid - and the consequence is that a guard nobody tests is a guard nobody
    knows works.

    So this class tests the fake directly, which nothing else does. It is not
    ceremony: through the normal paths the fake's check is *unreachable*, because
    the fold has already run and the adapter's guard is not even in the room. A
    test asserting that it refuses is the only thing keeping it from being dead
    code that reads like a defence.
    """

    @pytest.mark.parametrize(
        "recipient",
        ["+" + A_FOLDED_NUMBER, "234 801 234 5678", "", "a-number"],
        ids=["a-plus", "spaces", "empty", "letters"],
    )
    def test_it_refuses_a_recipient_outside_the_alphabet(self, recipient):
        with pytest.raises(SmsProviderError):
            FakeSmsChannel().send(AText(recipient=recipient))

    def test_it_refuses_a_national_spelling(self):
        with pytest.raises(SmsProviderError):
            FakeSmsChannel().send(AText(recipient=A_NATIONAL_SPELLING))

    def test_it_refuses_an_over_long_sender_id(self):
        channel = FakeSmsChannel(sender_id="x" * (MAX_SENDER_ID_LENGTH + 1))

        with pytest.raises(SmsProviderError):
            channel.send(AText())

    def test_a_recipient_in_the_folded_shape_is_accepted(self):
        """The other direction, so a fake that refused everything - and therefore
        made every application test fail for the wrong reason - could not pass."""
        channel = FakeSmsChannel()

        channel.send(AText(recipient=A_FOLDED_NUMBER))

        assert [message.recipient for message in channel.sent] == [A_FOLDED_NUMBER]

    def test_the_two_statements_of_the_rule_agree_on_what_is_accepted(self, posting):
        """**The assertion that makes the duplication worth paying for.**

        Two independent copies of a rule are only useful if they can be compared,
        and this is the comparison: one value the adapter sends and the fake takes,
        and one value both refuse.

        What it deliberately does *not* do is tie either copy to ``fold_phone`` -
        the values below are literals, so a fold that changed shape would leave this
        test passing. That division is the point rather than a gap: the domain test
        says what shape the fold produces, and this one says what shape both
        channels demand. A disagreement between them is therefore two failing tests
        naming the two ends of the wire, which is what the deposit bug would have
        looked like had it been visible at all.
        """
        build_channel().send(AText(recipient=A_FOLDED_NUMBER))
        assert posting.call["json"]["to"] == A_FOLDED_NUMBER
        FakeSmsChannel().send(AText(recipient=A_FOLDED_NUMBER))

        with pytest.raises(SmsProviderError):
            build_channel().send(AText(recipient=A_NATIONAL_SPELLING))
        with pytest.raises(SmsProviderError):
            FakeSmsChannel().send(AText(recipient=A_NATIONAL_SPELLING))
