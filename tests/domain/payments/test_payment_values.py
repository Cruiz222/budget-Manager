"""The three values a provider is described by, and what they refuse.

``PaymentIntent``, ``ProviderOutcome`` and ``ProviderAnswer`` are the vocabulary
of everything a payment provider can say. An intent is what comes back when a
collection is opened, an outcome is what arrives when it settles, and an answer is
what a lookup is told when somebody asks - and they are the boundary between
"bytes a third party sent" and "a fact this system acts on". The whole of what
they do is refuse to exist in a shape that cannot mean anything.

**That makes this file a list of refusals, which is the right shape for it.** A
value object with no behaviour has no happy path worth testing beyond the fields
it carries, and the fields it carries are asserted where they are used - by
``test_initiate_deposit.py`` on one side, ``test_settle_payment.py`` on the
other, and ``test_reconcile_payments.py`` for the third. What is only assertable
here is the constructor's own rules, and those exist so that a later reader of
``SettlePayment`` never has to wonder whether the reference it holds could be an
empty string.

``ProviderEvent`` and ``ProviderAnswerStatus`` get a test each at the bottom for
the one property worth pinning about an enum: that its *values* are this system's
words rather than Paystack's, which is the decision that keeps a vendor's dotted
string out of the domain.
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.payments.exception import (
    InvalidPaymentIntentError,
    InvalidProviderAnswerError,
    InvalidProviderOutcomeError,
)
from app.domain.payments.paymentIntent import PaymentIntent
from app.domain.payments.providerAnswer import ProviderAnswer
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome

NGN = Currency.NGN


def an_intent(url="https://checkout.paystack.test/abc", reference="ps_ref_1"):
    return PaymentIntent(authorization_url=url, provider_reference=reference)


def an_outcome(
    event=ProviderEvent.CHARGE_SUCCEEDED, reference="ps_ref_1", amount="5000"
):
    """An outcome, with the amount read the way ``Money`` demands one.

    ``Decimal`` rather than the raw argument, and that is not tidiness: ``Money``
    refuses a ``str`` outright, so a helper that passed one through would fail
    with ``InvalidMoneyOperationError`` from inside the value under test - an
    error about the *amount type* arriving in tests that are about the reference
    or the event. Found by running the suite, which is the only way this shows up:
    the parametrized refusals below all passed their own argument correctly and
    still died here first, which is how a broken helper looks.
    """
    return ProviderOutcome(
        event=event, reference=reference, amount=Money(Decimal(amount), NGN)
    )


def a_settled_answer(reference="ps_ref_1", amount="5000"):
    """An answer that says the money arrived, with the charge it arrived as."""
    return ProviderAnswer(
        ProviderAnswerStatus.SETTLED, an_outcome(reference=reference, amount=amount)
    )


class TestPaymentIntent:
    def test_it_carries_the_url_and_the_name(self):
        intent = an_intent()

        assert intent.authorization_url == "https://checkout.paystack.test/abc"
        assert intent.provider_reference == "ps_ref_1"

    def test_it_is_frozen(self):
        """**A reference that could be edited is a way to re-point a payment.**

        The reference in an intent is the key the ledger row is filed under, so a
        mutable intent would be a mutable pointer from a payment to a row. The
        assertion is on the mutation failing rather than on the dataclass being
        declared frozen, because it is the behaviour that matters.
        """
        intent = an_intent()

        with pytest.raises(FrozenInstanceError):
            intent.provider_reference = "somewhere else"

    @pytest.mark.parametrize("url", ["", "   ", None, 42])
    def test_it_refuses_a_url_that_is_not_one(self, url):
        with pytest.raises(InvalidPaymentIntentError):
            an_intent(url=url)

    @pytest.mark.parametrize("reference", ["", "   ", None, 42])
    def test_it_refuses_a_reference_that_is_not_one(self, reference):
        with pytest.raises(InvalidPaymentIntentError):
            an_intent(reference=reference)

    def test_two_intents_with_the_same_fields_are_equal(self):
        """A value object, so ``==`` is by value - which is what lets a fake and
        a real adapter's answers be compared in a test at all."""
        assert an_intent() == an_intent()


class TestProviderOutcome:
    def test_it_carries_the_three_facts_an_event_supplies(self):
        outcome = an_outcome()

        assert outcome.event is ProviderEvent.CHARGE_SUCCEEDED
        assert outcome.reference == "ps_ref_1"
        assert outcome.amount == Money(5000, NGN)

    def test_the_amount_is_required_and_not_defaulted(self):
        """**An outcome with no amount is a mismatch rule that never applies.**

        Settlement refuses to move anything when the event's amount disagrees
        with the row's, so an outcome that could not disagree would be the same
        thing as the rule silently not running. That is why the field has no
        default: the absence is refused here rather than defaulted away.
        """
        with pytest.raises(TypeError):
            ProviderOutcome(
                event=ProviderEvent.CHARGE_SUCCEEDED, reference="ps_ref_1"
            )

    @pytest.mark.parametrize(
        "event", ["charge.succeeded", None, 1], ids=["a wire name", "none", "a number"]
    )
    def test_it_refuses_an_event_that_is_not_a_provider_event(self, event):
        """The wire's own spelling is refused here, which is the seam working.

        ``"charge.success"`` is a real string Paystack sends and it is *not* a
        ``ProviderEvent`` - the translation happens at the route, so a value that
        got this far still carrying the vendor's name is a bug rather than an
        alternative input, and it is refused as one.
        """
        with pytest.raises(InvalidProviderOutcomeError):
            ProviderOutcome(
                event=event, reference="ps_ref_1", amount=Money(5000, NGN)
            )

    @pytest.mark.parametrize("reference", ["", "   ", None, 42])
    def test_it_refuses_a_reference_that_is_not_one(self, reference):
        with pytest.raises(InvalidProviderOutcomeError):
            an_outcome(reference=reference)

    def test_it_refuses_an_amount_that_is_not_money(self):
        with pytest.raises(InvalidProviderOutcomeError):
            ProviderOutcome(
                event=ProviderEvent.CHARGE_SUCCEEDED,
                reference="ps_ref_1",
                amount=5000,
            )

    @pytest.mark.parametrize("amount", [0, -1])
    def test_it_refuses_an_amount_that_is_not_a_movement(self, amount):
        """Zero and negative, and they are the same refusal for different reasons.

        A zero-amount event claims nothing arrived; a negative one claims money
        arrived in the wrong direction. Neither can describe a movement, and
        ``Money`` permits both - it has an ``is_debt`` property and a wallet can
        hold a negative balance - so the check has to live here rather than being
        inherited from the amount type.
        """
        with pytest.raises(InvalidProviderOutcomeError):
            an_outcome(amount=amount)


class TestProviderEvent:
    def test_the_values_are_our_words_and_not_the_wires(self):
        """**The decision this enum exists to make, asserted as one line.**

        Paystack writes ``charge.success``. Nothing in the domain may, which is
        why the members' values are underscored and read as English - so a search
        for the vendor's spelling finds exactly one table in one route file, and
        a second provider is a second table rather than a search across the
        codebase.
        """
        assert [event.value for event in ProviderEvent] == [
            "charge_succeeded",
            "transfer_succeeded",
            "transfer_failed",
            "transfer_reversed",
        ]

    def test_no_member_value_contains_a_dot(self):
        """The same claim stated as a property rather than as a list.

        A list would have to be edited by whoever added a fifth member, and the
        edit that added it would be the one least likely to notice. This fails on
        the new member itself.
        """
        assert not [e for e in ProviderEvent if "." in e.value]

    def test_the_two_directions_are_distinguishable_by_name(self):
        """``charge`` is money in, ``transfer`` is money out.

        Which is what ``SettlePayment`` dispatches on, and the reason a misspelt
        event name is a ``KeyError`` in a lookup table rather than a branch that
        falls through to whichever member happened to be written last.
        """
        incoming = [e for e in ProviderEvent if e.value.startswith("charge")]
        outgoing = [e for e in ProviderEvent if e.value.startswith("transfer")]

        assert incoming == [ProviderEvent.CHARGE_SUCCEEDED]
        assert len(outgoing) == 3


class TestProviderAnswer:
    """What a lookup answers with, and the one coupling it refuses to break.

    ``ProviderAnswer`` is the third value in this file and the first that can say
    "nothing happened" - an outcome is what a provider sends when something *did*,
    while an answer is what it says when asked and the truthful reply may be that
    there is nothing to report. The coupling - an outcome present exactly when the
    status is ``SETTLED`` - is the whole of what the constructor enforces, and it
    is enforced in both directions because both are ways to write a record that
    cannot mean anything.
    """

    def test_a_settled_answer_carries_the_charge(self):
        answer = a_settled_answer()

        assert answer.status is ProviderAnswerStatus.SETTLED
        assert answer.outcome.event is ProviderEvent.CHARGE_SUCCEEDED

    def test_the_two_quiet_answers_carry_no_outcome(self):
        assert (
            ProviderAnswer(ProviderAnswerStatus.NOT_SETTLED).outcome is None
        )
        assert (
            ProviderAnswer(ProviderAnswerStatus.NO_SUCH_REFERENCE).outcome is None
        )

    def test_it_refuses_a_settled_answer_with_no_outcome(self):
        """**The direction that would make settlement silently skip a payment.**

        A ``SETTLED`` answer with no outcome is a reconciler that was told the
        money had arrived and had nothing to hand the settler - so the row would
        stay PENDING for ever with the report claiming it was settled. The
        constructor refusing it is what makes every ``SETTLED`` in the codebase
        carry something a caller can act on.
        """
        with pytest.raises(InvalidProviderAnswerError):
            ProviderAnswer(ProviderAnswerStatus.SETTLED)

    @pytest.mark.parametrize(
        "status",
        [ProviderAnswerStatus.NOT_SETTLED, ProviderAnswerStatus.NO_SUCH_REFERENCE],
    )
    def test_it_refuses_an_outcome_on_an_answer_that_has_none(self, status):
        """**The other direction, and it is not symmetry for its own sake.**

        An outcome on a ``NOT_SETTLED`` answer is a charge that says money
        arrived attached to a status that says none did, and the two readings
        lead to opposite actions - credit or ignore. Refusing the record means
        there is never a moment where a caller has to decide which field to
        believe, which is the same rule ``Transaction`` applies to a
        ``completed_at`` on a PENDING row.
        """
        with pytest.raises(InvalidProviderAnswerError):
            ProviderAnswer(status, an_outcome())

    @pytest.mark.parametrize("status", ["settled", None, 1])
    def test_it_refuses_a_status_that_is_not_one(self, status):
        """A raw string is refused even when it spells a member's value, because
        the enum is the vocabulary - ``ProviderEvent``'s rule for its own
        members, one value over."""
        with pytest.raises(InvalidProviderAnswerError):
            ProviderAnswer(status)

    def test_two_answers_with_the_same_facts_are_equal(self):
        assert a_settled_answer() == a_settled_answer()

    def test_the_three_members_are_our_words_and_not_the_wires(self):
        """``success``, ``abandoned`` and a 404 are three spellings this domain
        never sees; the adapter is the only place that knows them. And
        ``NO_SUCH_REFERENCE`` is deliberately *not* named
        ``UNKNOWN_REFERENCE``: that member of ``SettlementOutcome`` means "no
        ledger row is filed under this reference", which is the opposite end of
        the wire from "the provider has no transaction under it"."""
        assert [status.value for status in ProviderAnswerStatus] == [
            "settled",
            "not_settled",
            "no_such_reference",
        ]
