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
