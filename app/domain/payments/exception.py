from app.domain.money.exception import MoneyError


class PaymentError(MoneyError):
    """Base for every rejection the payments domain makes."""


class InvalidTransferIntentError(PaymentError):
    pass

class PaymentProviderError(PaymentError):
    pass

class PaymentProviderUnavailableError(PaymentError):
    pass

class InvalidPaymentIntentError(PaymentError):
    """A provider answered with something that is not a usable intent.

    Nearly always a wrong secret key: Paystack answers ``401`` with
    ``{"message": "Invalid key"}`` and no ``data`` at all, so the adapter's parse
    is where that becomes legible rather than a ``KeyError`` three frames later.
    """


class InvalidProviderOutcomeError(PaymentError):
    """A set of facts about a provider event that cannot describe one.

    The amount, the reference and the event are each checked by
    ``ProviderOutcome``, so an event that reaches ``SettlePayment`` is a
    well-formed thing whatever else is true of it.
    """


class InvalidProviderAnswerError(PaymentError):
    """A set of facts about a provider's answer that cannot describe one.

    The sibling of ``InvalidProviderOutcomeError``, and it exists for the same
    reason one level up. An outcome is refused when the three things a provider
    told us cannot all be true at once; an answer is refused when the status and
    the outcome disagree about whether anything happened at all.

    Nearly always an adapter bug rather than a provider's doing - a provider
    answers in its own vocabulary and the adapter is what translates - which is
    why this is a distinct name rather than reusing the outcome's error. A
    traceback saying "invalid provider answer" points at the lookup call; one
    saying "invalid provider outcome" points at the webhook parse, and the two
    are different methods in a different file.
    """


class PayerEmailRefusedError(PaymentError):
    """The address this account would be billed under is not one a provider takes.

    Raised from two places, and the pair is the design rather than a duplication.
    ``app.domain.payments.payerEmail`` refuses an address that plainly cannot work
    *before* a request is built, and the adapter raises this same error when
    Paystack answers ``invalid_email_address`` - so a client is told one thing
    whichever caught it, and correctness never rests on this codebase's reading of
    the provider's rule. The local check makes the common case cheap; the provider
    remains the authority.

    It is not an identity error, and deliberately not raised by ``User``. The
    account is a perfectly good account; what is unusable is the address, in the
    one role that needs a domain a provider will bill. That asymmetry is why the
    rule could not simply be tightened at signup - it would refuse people this
    system has no complaint about.

    Graded a 400 by falling through ``errors._grade``, like every other refusal
    this domain makes.
    """


class PayerEmailMissingError(PaymentError):
    """The account holds no address, so there is nothing to be billed under.

    **Not a refusal about a *value*, which is why it is not
    ``PayerEmailRefusedError``.** That one says the address an account holds is
    not one a provider will take; this one says there is no address at all. The
    two read alike from a distance and their sentences are not interchangeable:
    one person has something to correct, the other has a field to fill in, and an
    error name that conflated them would make the API tell half its callers the
    wrong thing.

    **It exists because an address became optional on an account**, which is what
    lets somebody sign up with a phone number alone. Such an account is complete
    and functional - a wallet, a password, a session, a number it can be reached
    at - right up to the point it has to be *billed*, and a provider is handed the
    payer's address. So this is not the account being half-built; it is one
    operation needing one field that another does not.

    The remedy is named in the sentence rather than left to be discovered, and it
    is the email-change request - which for an account holding no address *sets*
    one rather than moving one, and is the only route by which such an account
    becomes able to take money. See ``RequestEmailChange``.

    A 400 by falling through ``errors._grade``, like every other refusal this
    domain makes: what is wrong is the account the caller is asking on behalf of,
    and it is theirs to change.
    """


class CurrencyNotCollectableError(PaymentError):
    """This wallet holds a currency the payment rail cannot collect in.

    **The first refusal here that is ours rather than the provider's, and the
    distinction is the whole of why it is a name instead of a
    ``PaymentProviderError``.** That one reports something the far end did; the
    other names in this module are facts about the account this side holds. This
    one is this side reading a fact the *rail* supplied - ``supported_currencies``
    - and refusing on it, so nothing has been sent and no payer has been anywhere.
    The remedy belongs to whoever holds the wallet rather than to whoever answers
    the phone at Paystack.

    **It belongs to the payments tree rather than beside ``WalletClosedError``**,
    which is the other refusal shaped like it. A closed wallet is the money
    domain's own statement about a wallet; this is a statement about the meeting
    of a wallet and a *rail*, and the money domain has no opinion about what a
    rail can collect - it holds every currency ``Currency`` names, and after
    decision 267 that is ``NGN`` and ``USD``. Filing this beside
    ``CurrencyMismatchError`` would say the domain knew, and it does not. See
    ``PaymentProvider.supported_currencies``.

    **Since the trim, ``USD`` is the only currency that can reach here**, and that
    is what makes this a *live* refusal rather than a defensive one: the currency
    list no longer contains anything the rail's account has never heard of, so the
    one remaining case is the one that was always the point - a currency this
    system holds happily and this account cannot collect.

    **A 409, and the two neighbours it is worth ruling out.** Not a 400: the
    request is well formed, the wallet is the caller's, and there is nothing in
    it to correct - telling a client to fix a good request is the mistake
    ``errors`` already argues against for a wallet that does not exist. Not a
    503: this installation serves deposits perfectly well, and a client that read
    one here would take the whole installation offline over a single wallet. 409
    because the wallet exists and *its own state* refuses this, which is exactly
    the row ``WalletClosedError`` sits on.

    **And it is deliberately not a refusal at wallet creation.** A wallet in a
    currency this rail cannot collect is still a wallet - it can be funded
    directly, it can hold pots and plans, and a second rail or an account enabled
    for a second currency changes nothing about it. What cannot happen is a
    *collection* for it, which is a fact about one door rather than about the
    wallet, so it is refused at that door.
    """


class DepositAlreadyInitiatedError(PaymentError):
    pass

class InvalidVirtualAccountError(PaymentError):
    pass