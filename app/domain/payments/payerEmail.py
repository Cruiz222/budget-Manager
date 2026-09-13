"""What a payment provider requires of the address it is told to bill.

**This is a courtesy, and the provider is the guard.** An account's email is what
``InitiateDeposit`` hands a provider as the payer address - its ``execute``
docstring says why it comes from the account rather than from the request - and no
provider will bill every string this system accepts as an email. The account rule
is deliberately minimal (``User`` requires only an ``@``), so ``live@localhost``
is a legal account here and an address Paystack refuses.

Two checks exist and they are not the same check. This one refuses an address that
*plainly* cannot work, before a request is built, with a sentence written for a
person. The provider's refusal is the authority and does not depend on this file at
all: the adapter translates it into the same error (``PayerEmailRefusedError``), so
a client is told one thing whichever caught it.

**The local rule is the narrowest one the evidence supports, and the asymmetry is
the reason.** A courtesy check that guesses wrong in the *permissive* direction
costs one provider call and a legible refusal. One that guesses wrong in the
*strict* direction refuses a legitimate user's money, which this system cannot
recover from on their behalf. So it refuses only what it can be certain about - no
``@`` at all, or no dot after it - and hands everything else to the provider to
judge. This is decision 143's shape one job over: the window is a courtesy and the
four settled events are the guard.

Where the rule came from, because it is not invented. A live call carrying
``live@localhost`` returned

```
{"message":"Invalid Email Address Passed","code":"invalid_email_address"}
```

while the same call with ``live@example.com`` returned a checkout URL. That pair is
the whole of the evidence, which is why this refuses a missing dot rather than
attempting anything cleverer - a dot is no guarantee a provider will accept, and
that is precisely why this is not the guard.
"""

from app.domain.payments.exception import PayerEmailRefusedError


def refuse_unusable_payer_email(email: str) -> None:
    """Raise ``PayerEmailRefusedError`` for an address no provider will bill.

    Returns nothing rather than a bool, which is ``app.domain.money.reference``'s
    shape one package over: a caller that has to *check* a return value can forget
    to, and the refusal is the whole of the contract. Called by
    ``InitiateDeposit._prepare``, before anything leaves the process.
    """
    _, at, domain = email.rpartition("@")
    if at and "." in domain:
        return

    raise PayerEmailRefusedError(
        f"{email!r} cannot be used as a payer address; a payment provider needs "
        f"an address with a real domain"
    )
