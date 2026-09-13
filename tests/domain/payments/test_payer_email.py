"""``refuse_unusable_payer_email``: the courtesy check, and why it is only that.

One rule is pinned here and one *property* of it, and the property is the
interesting half. The rule is that an address no provider could bill is refused
before a request is built. The property is that the rule is deliberately
permissive rather than clever, because its two ways of being wrong are not
symmetrical: letting a bad address through costs one provider call and a legible
refusal, while refusing a good one stops somebody depositing - a failure this
system cannot undo on their behalf.

**The provider is the guard, and is not tested here.** What Paystack actually
accepts is only knowable by asking Paystack, which is what the live run in the
README does and what found this rule in the first place. This file pins the
courtesy; ``tests/infrastructure/payments/test_paystack_payment_provider.py``
pins the translation of the provider's own refusal into this same error, which is
what makes the guard real rather than a second opinion.
"""

import pytest

from app.domain.identity.emailAddress import (
    has_real_domain,
    refuse_unusable_email,
)
from app.domain.identity.exception import UnusableEmailError
from app.domain.money.exception import MoneyError
from app.domain.payments.exception import PayerEmailRefusedError
from app.domain.payments.payerEmail import refuse_unusable_payer_email


def test_an_address_with_a_real_domain_is_left_alone():
    """The common case, and it must cost nothing.

    Every account a person actually creates looks like this, so the check has to
    be silent for all of them. A courtesy that fires on a legitimate address is
    the one failure this shape exists to avoid.
    """
    refuse_unusable_payer_email("payer@example.com")


def test_an_address_with_no_domain_at_all_is_refused():
    """``live@localhost``, which is the address a live run was refused for.

    Not ``localhost`` in particular - the rule is the missing dot, because that is
    the whole of the evidence: one address refused and one accepted, differing by
    exactly that.
    """
    with pytest.raises(PayerEmailRefusedError):
        refuse_unusable_payer_email("live@localhost")


def test_an_address_with_no_at_sign_is_refused():
    """``User`` requires an ``@``, so this cannot arrive from the API today.

    The function does not assume its caller, though, and a rule that trusted one
    would break the day a second caller appears - which is the mistake the
    reference rule made, and the reason it now lives in one place.
    """
    with pytest.raises(PayerEmailRefusedError):
        refuse_unusable_payer_email("nobody")


def test_the_refusal_names_the_address_and_what_would_have_been_accepted():
    """Written for the person holding the account, like every domain refusal here.

    The same rule ``InvalidIdempotencyKeyError`` follows: a refusal that names the
    value and the shape it wanted is one a client can act on without reading this
    codebase. It is the opposite of what the provider says, whose answer names a
    parameter and sends the reader looking at the wrong system.
    """
    with pytest.raises(PayerEmailRefusedError) as refused:
        refuse_unusable_payer_email("live@localhost")

    assert "live@localhost" in str(refused.value)
    assert "real domain" in str(refused.value)


def test_an_odd_but_dotted_address_is_left_to_the_provider():
    """**The property that matters, and it is a decision rather than an oversight.**

    ``a@b.`` and ``a@.`` are almost certainly addresses nobody will bill, and this
    check passes them both - deliberately. Guessing wrong in this direction costs
    one provider call and a refusal that names the address. Guessing wrong in the
    other direction refuses somebody's deposit, which is not a failure this system
    can recover from on their behalf. So the courtesy refuses only what it can be
    certain about and hands everything else to the authority.
    """
    refuse_unusable_payer_email("a@b.")
    refuse_unusable_payer_email("a@.")


def test_it_answers_with_nothing_rather_than_a_verdict():
    """A caller cannot forget to check a return value that does not exist.

    ``app.domain.money.reference``'s shape one package over, for the same reason:
    the refusal *is* the contract, so there is no second way to call this that
    quietly skips the check.
    """
    assert refuse_unusable_payer_email("payer@example.com") is None


def test_the_refusal_is_a_money_error_so_every_presentation_renders_it():
    """Which is what makes a 400 the API's answer and an ``error:`` line the CLI's.

    ``errors._grade`` grades an unlisted ``MoneyError`` as a 400, so a refusal
    deriving from ``Exception`` instead would escape both presentations as a
    traceback - and the deposit route is not the only caller this could ever have.
    """
    assert issubclass(PayerEmailRefusedError, MoneyError)


#: Addresses chosen so that every branch of the predicate is present: a real
#: domain, a domain without a dot, two spellings of "no ``@`` at all", the two
#: dotted-but-odd values this file already passes on purpose, and a pair that
#: differ only in which side of it their *last* ``@`` puts the dot.
SHARED_PREDICATE_CASES = (
    "payer@example.com",
    "a@b.",
    "a@.",
    "live@localhost",
    "payer@LOCALHOST",
    "nobody",
    "",
    "@localhost",
    "a@b@example.com",
    "a@b@localhost",
)


def refuses(refusal, email: str) -> bool:
    """Whether ``refusal`` refuses ``email``, without caring *which* class it raises.

    Catching both refusals and answering a boolean is what makes the test below
    about agreement rather than about either rule. The classes are asserted where
    they belong - ``test_the_two_refusals_are_two_errors_on_purpose`` just below,
    and the identity module's own file - and a test that compared exception types
    here would be testing those instead of this.
    """
    try:
        refusal(email)
    except (PayerEmailRefusedError, UnusableEmailError):
        return True
    return False


class TestTheTwoRefusalsAreOneRule:
    """The entry rule and the payment courtesy, pinned against each other.

    ``refuse_unusable_email`` and ``refuse_unusable_payer_email`` are the same
    predicate read two ways - a guard where an address is minted, a courtesy where
    one is handed to a provider - and they exist as two functions because the two
    readings have different *error classes* and different sentences. What they must
    never have is different *opinions*, and that is the whole of what this class
    asserts.

    It is worth a test rather than a comment because the failure mode is silent and
    asymmetric. If somebody tightened one of the two - the obvious temptation is a
    regex at the entry rule, where guessing wrong is cheap - the system would end up
    refusing to create an account it would happily try to bill, or the reverse. The
    first is a person who cannot register, the second is a person whose first
    deposit is refused by a third party weeks later, which is the trap this whole
    slice was built to close. Neither would fail any other test in the suite.
    """

    @pytest.mark.parametrize("address", SHARED_PREDICATE_CASES)
    def test_the_two_refusals_agree_about_every_address(self, address):
        assert refuses(refuse_unusable_payer_email, address) == refuses(
            refuse_unusable_email, address
        )

    @pytest.mark.parametrize("address", SHARED_PREDICATE_CASES)
    def test_and_both_agree_with_the_predicate_they_share(self, address):
        """The stronger half: not merely "the two agree", but *about what*.

        Two functions that both refused everything would pass the test above, and so
        would two that both refused nothing. This pins them to the one predicate the
        module docstring says they delegate to, so the only way to change either
        behaviour is to change ``has_real_domain`` - which is the arrangement, and
        the reason the rule lives in ``identity`` rather than being copied.
        """
        assert refuses(refuse_unusable_email, address) is (
            has_real_domain(address) is False
        )

    def test_the_two_refusals_are_two_errors_on_purpose(self):
        """Same input, same opinion, two classes - and the split is the two readings.

        A caller catching ``PayerEmailRefusedError`` is catching a fact about a
        provider; one catching ``UnusableEmailError`` is catching a fact about the
        address an account may hold. Collapsing them would make the deposit route
        unable to tell which of the two it was reporting, and the API's ``error``
        field is what a client branches on.
        """
        with pytest.raises(PayerEmailRefusedError):
            refuse_unusable_payer_email("live@localhost")

        with pytest.raises(UnusableEmailError) as refused:
            refuse_unusable_email("live@localhost")

        assert not isinstance(refused.value, PayerEmailRefusedError)

    def test_the_two_sentences_say_different_things_about_the_same_address(self):
        """Both name the address; each names the authority it is speaking for.

        The payment refusal talks about what "a payment provider needs", because the
        reader is somebody trying to deposit and the next actor in the story is a
        provider. The identity refusal talks about the domain the *address* needs,
        because the reader is somebody at a form and the next actor is them. One
        rule, two audiences - which is the whole reason there are two sentences
        rather than one shared string.
        """
        with pytest.raises(PayerEmailRefusedError) as payer:
            refuse_unusable_payer_email("live@localhost")
        with pytest.raises(UnusableEmailError) as identity:
            refuse_unusable_email("live@localhost")

        assert "live@localhost" in str(payer.value)
        assert "live@localhost" in str(identity.value)
        assert "provider" in str(payer.value)
        assert str(payer.value) != str(identity.value)
