"""The one place a caller's idempotency key becomes a ledger reference.

**Why a key has to be namespaced at all.** ``get_by_internal_reference`` is a
global lookup over a globally UNIQUE column, and ``WalletOperation.execute``
returns the row it finds without asking whose wallet it belongs to. Two people
choosing the same key therefore do not merely collide - the second one receives
the first one's transaction back as though it were their own. Prefixing the key
with the wallet it is spent from closes that, and the prefix is derived from a
wallet the caller has already been proved to own, so it is not client input and
cannot be chosen: a caller who sends ``"<somebody else's wallet uuid>.x"`` gets
that string as *their* key, prefixed again with their own wallet, which is a key
nobody else will ever use and not a way into anyone's namespace.

The namespace is the **wallet**, not the actor, and that is the tighter of the
two rather than a different idea. A wallet has exactly one owner, so a
wallet-scoped key is also an actor-scoped one; and it additionally closes a case
scoping by owner would leave open - one user posting the same key to two of
their own wallets, where the second would silently return the first's
transaction and leave the second wallet untouched.

**It is written once, and that is a fix rather than tidiness.** This rule used to
exist twice - here as ``WalletService._scoped_reference``, and inline in
``InitiateDeposit._prepare`` - and the two copies had already drifted by the time
anyone looked: the deposit copy is the one that went on the wire, and it went
there carrying a ``:`` that Paystack refuses, so every deposit this system ever
attempted against the real provider was rejected. A rule with two homes is a rule
that will disagree with itself.

**The separator and the alphabet are one decision, taken together, because both
ends of the string have to accept the result.** A reference built here is two
different things depending on who is asking: for a withdrawal it is only ever a
ledger's name, and for a deposit it is *also* what we hand the payment provider
as its own idempotency key - see ``PaymentProvider.initialize_deposit``, where
that identity is the whole of the retry story. So the shape is chosen to be
acceptable to the stricter end, which is the provider's, and every reference this
function mints is therefore one a provider would take.
"""

from string import ascii_letters, digits
from uuid import UUID

from app.domain.money.exception import InvalidIdempotencyKeyError

#: What joins the wallet to the key.
#:
#: A dot, and the choice is narrower than it looks. The separator has to be a
#: character a payment provider accepts, which rules out the ``:`` this used to
#: be, and it has to be one a wallet id cannot contain, or the string would not
#: be unique per ``(wallet, key)`` pair. A hyphen fails that second test - wallet
#: ids are UUIDs, and UUIDs are made of hyphens - while a dot passes both: the
#: first dot in the result is always the separator, because the 36 characters
#: before it are a canonical UUID and hold none. Nothing parses the string back
#: apart today, and this is what makes that safe to rely on if anything ever
#: does.
REFERENCE_SEPARATOR = "."

#: The characters a key may be made of.
#:
#: Letters and digits, plus ``-``, ``.``, ``,`` and ``=``. This is not a
#: preference; it is the set the payment provider accepts, quoted from its own
#: rejection of the string this code used to build: *"Ensure that you aren't
#: using any characters that aren't alphanumeric or contained in '-,., =' in
#: your reference"*. A key outside it is refused here, before a request is built
#: and long before it leaves the process, which is the only place the refusal can
#: carry a sentence that names the problem - the provider's answer is a bare 400
#: that reads as though this system is broken.
REFERENCE_ALPHABET = frozenset(ascii_letters + digits + "-.,=")


def scoped_reference(wallet_id: UUID, key: str) -> str:
    """Namespace ``key`` to ``wallet_id``, or refuse it as unusable.

    Two consequences worth stating rather than discovering:

    - **Stored references have a shape, and it changed.** Rows written before
      this function existed read ``"<wallet uuid>:<key>"``, so a retry of a key
      from back then no longer matches once. That is the change working, not a
      regression - and it is the same consequence the separator's previous move
      carried.
    - **The reference in a response is not the key to retry with.** A client sees
      ``"<wallet uuid>.<the key they sent>"``, and sending *that* back as a new
      ``ref`` would create a second transaction - for a withdrawal, a double
      spend. The key is the one the caller generated.

    ``ExecutePlanRun`` does not come through here, and does not need to: it builds
    its operations directly and its references already embed ``plan_id``, which is
    unique and server-minted. A client key can never equal one, because the
    client's is namespaced and the plan's is not - which is also why the alphabet
    below is not a rule about ``Transaction``: a plan's reference is full of
    colons and always will be, and no provider ever sees it.
    """
    _refuse_unsendable(key)
    return f"{wallet_id}{REFERENCE_SEPARATOR}{key}"


def _refuse_unsendable(key: str) -> None:
    """Raise for a key that could not be put in a reference, naming what is allowed.

    **Checked here rather than at the edge, and the placement is the point.** The
    rule belongs to the string being built, so it holds for every caller - the API
    today, the CLI, and whatever opens a collection next - rather than only for
    the one door that remembered to check. The API's schemas stay shape-only, as
    their own docstring promises, and a client is answered by the same
    ``{"error", "detail"}`` body every other domain refusal produces, with a
    ``detail`` written to be read by a person.

    There is deliberately no length rule beside the character rule. The provider
    documents no limit, and inventing one would refuse keys that work.
    """
    refused = sorted(
        {character for character in key if character not in REFERENCE_ALPHABET}
    )
    if refused:
        raise InvalidIdempotencyKeyError(
            f"the idempotency key contains {''.join(refused)!r}, which a payment "
            f"provider refuses; letters, digits and - . , = only"
        )
