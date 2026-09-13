"""``has_real_domain``: one predicate, read three ways, pinned twice.

The rule under test is one line long - is there a dot after the last ``@`` - and
the interesting thing about it is not the line. It is that the same line is a
**guard** at the two places this system mints an address and a **courtesy** at the
one place an address is handed to a provider, and that those two readings want
opposite things from it.

A guard may be strict, because being wrong costs a retry: the person is at a form,
holding an address they already use, and "type a different one" is a complete
remedy. A courtesy may not, because being wrong costs a deposit somebody's money
is already behind - and this system cannot put it back on their behalf. So the
predicate is built as narrow as the evidence allows and the *callers* decide how
much weight to put on it.

That asymmetry is why this file tests both directions of the same function. The
refusals are here; so is the acceptance of an address nobody could bill, because
"deliberately permissive" is a claim that would otherwise be indistinguishable
from a bug.

**The evidence, which is the whole of the rule's justification.** A live call
carrying ``live@localhost`` returned

```
{"message":"Invalid Email Address Passed","code":"invalid_email_address"}
```

and the same call with ``live@example.com`` returned a checkout URL. Two addresses,
one field different, two opposite answers. That is not a grammar and it is not
worth inventing one from - it is one character's worth of knowledge, and the
predicate checks one character.

``refuse_unusable_email`` is pinned beside it, because the predicate answering a
question and the refusal raising are two different contracts: the first a caller
could ignore, the second it cannot.
"""

import pytest

from app.domain.identity.emailAddress import has_real_domain, refuse_unusable_email
from app.domain.identity.exception import IdentityError, UnusableEmailError
from app.domain.money.exception import MoneyError

#: Addresses the predicate accepts, including the two in the commentary above and
#: two that are almost certainly unbillable.
#:
#: The last pair is the point of the list rather than an oversight. A test that
#: only asserted the accepted values were *plausible* would pass for a predicate
#: that had grown a grammar, and a grammar here is exactly the mistake - every
#: extra rule is a guess about a provider this system does not control.
ACCEPTED = (
    "live@example.com",
    "ada@example.com",
    "a@b.",
    "a@.",
)

#: Addresses the predicate refuses, for two different reasons.
REFUSED = (
    "live@localhost",  # the live finding: a domain with no dot in it
    "nobody@localhost",
    "payer@LOCALHOST",
    "not-an-address",  # no ``@`` at all
    "nobody",
    "",
    "@localhost",
)


@pytest.mark.parametrize("email", ACCEPTED)
def test_an_address_with_a_dot_in_its_domain_is_accepted(email):
    assert has_real_domain(email) is True


@pytest.mark.parametrize("email", REFUSED)
def test_an_address_without_one_is_refused(email):
    assert has_real_domain(email) is False


def test_the_domain_is_what_follows_the_last_at_sign():
    """``rpartition`` and not ``split``, and this is the case that tells them apart.

    An address may legally contain more than one ``@`` - a quoted local part may -
    so the domain a mail system reads is everything after the *final* separator.
    ``split`` would take the first, report ``b`` as the domain, and accept an
    address whose real domain has no dot in it.

    Written as a pair, because one call cannot show the difference: both spellings
    of the separator agree on the single-``@`` case, and disagree only here.
    """
    assert has_real_domain("a@b@example.com") is True
    assert has_real_domain("a@b@localhost") is False


def test_an_address_with_no_at_sign_answers_false_rather_than_raising():
    """The ``at`` guard, which exists because the predicate does not assume its caller.

    ``rpartition`` returns ``("", "", "nobody")`` when the separator is absent, so
    ``domain`` becomes the whole string and a bare ``"." in domain`` test would
    accept it. ``User`` refuses such a value long before this - but a rule that
    trusted one caller is the mistake the reference rule made in the same week, and
    the reason this predicate lives in one place now.

    ``""`` is in the refused list for a reason worth naming: it is what a caller
    that read the wrong field would pass, and it must not read as "no dot found,
    therefore fine".
    """
    assert has_real_domain("nobody") is False
    assert has_real_domain("") is False


def test_it_accepts_an_address_it_cannot_be_certain_about():
    """**The narrowness, asserted rather than left to be assumed.**

    ``a@b.`` and ``a@.`` are addresses no provider will bill and this predicate
    passes them both. That is a decision, and it is the one that separates a
    courtesy from a guard: refusing here would be refusing a *deposit*, and the
    evidence supports exactly one character, so one character is what is checked.

    The same input at the deposit site is refused a moment later by the provider,
    which is the authority - and the adapter translates that into the same error a
    client would have seen from this rule, so nothing is lost but one round trip.
    """
    assert has_real_domain("a@b.") is True
    assert has_real_domain("a@.") is True


def test_the_refusal_names_the_address_and_the_shape_it_wanted():
    """Written for the person holding the account, like every refusal here.

    The opposite of what a provider says. Paystack's answer to this is
    ``Invalid Email Address Passed`` with a ``nextStep`` about passing the ``email``
    parameter, which reads as though this codebase forgot to send one - and sends
    the reader looking at the wrong system entirely.
    """
    with pytest.raises(UnusableEmailError) as refused:
        refuse_unusable_email("live@localhost")

    assert "live@localhost" in str(refused.value)
    assert "dot" in str(refused.value)


def test_an_address_with_a_real_domain_is_left_alone():
    """The common case, and it must cost nothing.

    Every account a person actually creates looks like this. A guard that fired on
    a legitimate address would refuse an address its owner already holds and
    already receives mail at.
    """
    assert refuse_unusable_email("live@example.com") is None


def test_it_answers_with_nothing_rather_than_a_verdict():
    """A caller cannot forget to check a return value that does not exist.

    ``app.domain.money.reference``'s shape and
    ``refuse_unusable_payer_email``'s beside it: the refusal *is* the contract, so
    there is no second way to call this that quietly skips the check.
    """
    assert refuse_unusable_email("ada@example.com") is None


def test_the_refusal_is_a_money_error_so_every_presentation_renders_it():
    """Which is what makes a 400 the API's answer and an ``error:`` line the CLI's.

    ``errors._grade`` grades an unlisted ``MoneyError`` as a 400, so this needs no
    entry in that module to behave like every refusal before it - and a refusal
    deriving from ``Exception`` instead would escape both presentations as a
    traceback, at an endpoint an unauthenticated caller can reach.
    """
    assert issubclass(UnusableEmailError, IdentityError)
    assert issubclass(UnusableEmailError, MoneyError)


def test_it_does_not_decide_anything_by_looking_at_a_store():
    """Whether an address is *taken* is a different question with a different error.

    ``DuplicateEmailError`` is asked by the use case that has a store to ask, and
    the split matters at an endpoint an unauthenticated caller can reach: a check
    here that consulted anything would let that caller learn which addresses are
    registered by watching which refusal they got.
    """
    assert refuse_unusable_email("ada@example.com") is None

    # And the same address is refused by the *other* rule when it is somebody's.
    with pytest.raises(UnusableEmailError):
        refuse_unusable_email("ada@localhost")
