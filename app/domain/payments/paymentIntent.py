from dataclasses import dataclass

from app.domain.payments.exception import InvalidPaymentIntentError


@dataclass(frozen=True)
class PaymentIntent:
    """What a provider hands back when it agrees to collect money for us.

    Two fields, and both are load-bearing:

    - ``authorization_url`` is where the *payer* is sent. It is the whole reason
      this call exists: the wallet cannot collect a card payment, so it asks the
      party that can to host a page, and hands the client a link to it.
    - ``provider_reference`` is how the provider will name this movement when it
      writes to us later. It is recorded on the ledger row, and it is the only
      thing an arriving webhook can be matched against - a webhook knows a
      reference and nothing else about us.

    **Frozen, and that is not decoration.** The reference in here is the key the
    ledger row is filed under, so a value that could be edited after the fact
    would be a way to re-point a payment at another row. Every value object in
    this codebase is frozen for the same reason; this is the one where it costs
    the most to be wrong.

    Note what is *not* here: an amount, a status, a wallet. This is a receipt for
    a conversation with a third party - "here is your link, here is the name I
    will use" - and nothing about our own side of it, which the ledger row
    already holds.
    """

    authorization_url: str
    provider_reference: str

    def __post_init__(self):
        if not isinstance(self.authorization_url, str) or not self.authorization_url.strip():
            raise InvalidPaymentIntentError(
                "authorization_url must be a non-empty string"
            )

        if not isinstance(self.provider_reference, str) or not self.provider_reference.strip():
            raise InvalidPaymentIntentError(
                "provider_reference must be a non-empty string"
            )
