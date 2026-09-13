"""``scoped_reference``: the one place a caller's key becomes a ledger reference.

Two things are pinned here and they are different sizes. The small one is the
shape - a key is spent from a wallet, so the reference names the wallet. The large
one is the **alphabet**, which is a payment provider's, and which is the reason
this function exists at all: the same rule written in two places had already
drifted, and the copy that drifted was the one that reached the wire.

**The alphabet below is spelled out rather than imported**, and the duplication is
the point. A test that imports the constant it is checking agrees with the code by
construction instead of by being right - which is exactly how a suite of two
thousand passing tests sat on top of a deposit route that had never once worked
against the real provider. ``tests/conftest.py`` keeps its own copy for the same
reason, in the double that talks to the deposit path.

Neither copy can prove the rule is still Paystack's. Only Paystack can, and that
is what the end-to-end run in the README is for; what these two can prove is that
the code and the double still agree with the rule as it was written down when a
live 400 spelled it out.
"""

from uuid import UUID

import pytest

from app.domain.money.exception import InvalidIdempotencyKeyError, MoneyError
from app.domain.money.reference import REFERENCE_SEPARATOR, scoped_reference

#: What a payment provider accepts in a reference, written out by hand.
#:
#: Quoted from Paystack's rejection of the string this code used to build:
#: *"Ensure that you aren't using any characters that aren't alphanumeric or
#: contained in '-,., =' in your reference"*. The space their sentence shows
#: inside the quotes is read as punctuation rather than as a member of the set -
#: see the test below that says so out loud, because a reader who assumes the
#: other way would find this constant wrong rather than the sentence ambiguous.
SENDABLE = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "-.,="
)

#: A fixed wallet, so that an expected reference can be read at a glance.
WALLET = UUID("11111111-2222-3333-4444-555555555555")

#: A second one, for the tests about two wallets not colliding.
OTHER_WALLET = UUID("99999999-8888-7777-6666-555555555555")


# --- the shape --------------------------------------------------------------

def test_a_key_is_namespaced_to_the_wallet_it_is_spent_from():
    """The whole function, in one line.

    What it buys is not tidiness: ``get_by_internal_reference`` is a global lookup
    over a globally unique column, so two callers choosing the same word would
    share a row - and the second would be handed the first one's transaction as
    though it were their own.
    """
    assert scoped_reference(WALLET, "invoice-7") == f"{WALLET}.invoice-7"


def test_two_wallets_may_use_one_key_without_colliding():
    """The leak closed, stated from the side a caller can see.

    The scope is the wallet rather than the owner because a wallet has exactly one
    owner - and because scoping by owner would leave one case open, where the same
    person posts one key to two of their own wallets.
    """
    assert scoped_reference(WALLET, "invoice-7") != scoped_reference(
        OTHER_WALLET, "invoice-7"
    )


def test_the_first_separator_is_the_separator_even_when_the_key_has_one():
    """Which is why a key is allowed to contain the separator at all.

    A wallet id is a UUID, and a UUID has no dots in it - so the first dot in the
    result is always this function's, and the two halves can be told apart even
    though nothing parses them apart today. A hyphen would fail this: wallet ids
    are made of hyphens, and ``"<uuid>-<key>"`` is not recoverable.
    """
    minted = scoped_reference(WALLET, "a.b")

    wallet_text, separator, key_text = minted.partition(REFERENCE_SEPARATOR)

    assert wallet_text == str(WALLET)
    assert separator == REFERENCE_SEPARATOR
    assert key_text == "a.b"


# --- the alphabet -----------------------------------------------------------

def test_the_separator_is_a_character_a_provider_accepts():
    """**The assertion that would have caught the bug at the bottom of this file.**

    A reference is two things at once for a deposit - the ledger's name for a
    movement and the idempotency key handed to Paystack - so the separator has to
    be sendable. It was a colon for the whole life of the deposit route, and
    Paystack refuses colons, so every deposit that route ever attempted was
    rejected at the far end while this suite stayed green.
    """
    assert REFERENCE_SEPARATOR in SENDABLE
    assert REFERENCE_SEPARATOR not in str(WALLET)


def test_every_legal_key_produces_a_reference_a_provider_accepts():
    """Stated as a property over the whole alphabet rather than over one sample."""
    for character in sorted(SENDABLE):
        assert set(scoped_reference(WALLET, f"k{character}k")) <= SENDABLE


def test_a_key_with_a_colon_is_refused():
    """Refused *here*, which is the only place that can refuse it usefully.

    The rule belongs to the string being built rather than to the door that
    happened to remember to check, so it holds for every caller - and the refusal
    can say what is wrong, which the provider's own 400 never does.
    """
    with pytest.raises(InvalidIdempotencyKeyError):
        scoped_reference(WALLET, "order:42")


def test_the_refusal_names_the_character_and_what_would_have_been_allowed():
    """Because the person reading it is holding a key they chose themselves.

    The message is written for that person rather than for a log, which is the
    same rule every domain refusal in this codebase follows - and the opposite of
    what the provider says, whose answer names a field and sends the reader
    looking at the wrong system.
    """
    with pytest.raises(InvalidIdempotencyKeyError) as refused:
        scoped_reference(WALLET, "order:42")

    assert ":" in str(refused.value)
    assert "- . , =" in str(refused.value)


def test_a_space_is_refused_and_that_is_a_reading_of_an_ambiguous_sentence():
    """A decision rather than an oversight, said out loud.

    Paystack's ``nextStep`` spells the allowed set as ``-,., =``, and the space in
    it may be a typo or may be part of the set. Refusing it is the safe side of
    that: no reference this system mints contains one, and a key that did would
    only be refused later, by a 400 nobody could read.
    """
    with pytest.raises(InvalidIdempotencyKeyError):
        scoped_reference(WALLET, "order 42")


def test_the_refusal_is_a_money_error_so_every_presentation_renders_it():
    """Which is what makes a 400 and an ``error:`` line fall out for free.

    ``app.presentation.api.errors`` grades an unlisted ``MoneyError`` as a 400 and
    the CLI catches the same root at the top of ``main``. A refusal derived from
    ``Exception`` instead would escape both as a traceback - and the deposit route
    is not the only caller of this function, so it would escape from whichever
    door came next.
    """
    assert issubclass(InvalidIdempotencyKeyError, MoneyError)
