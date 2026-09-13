from dataclasses import dataclass
from uuid import UUID

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.money.exception import (
    CurrencyMismatchError,
    InvalidAmountError,
    WalletClosedError,
)
from app.domain.money.money import Money
from app.domain.money.reference import scoped_reference
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.exception import DepositAlreadyInitiatedError
from app.domain.payments.payerEmail import refuse_unusable_payer_email
from app.domain.payments.paymentProvider import PaymentProvider


@dataclass(frozen=True)
class InitiatedDeposit:
    """A deposit that has been asked for and has not happened.

    Every field here is a fact about a *request*, and that is the whole point of
    the type existing rather than the route returning a ``Transaction``. The row
    is PENDING when this is built and stays PENDING until a webhook settles it;
    ``status`` is carried so a client is told that in so many words rather than
    having to infer it from the absence of a balance change.

    ``authorization_url`` is the one field that is not ours. It is the provider's
    page, it is where the payer goes, and nothing in this system may parse it,
    store it or reconstruct it.
    """

    authorization_url: str
    provider_reference: str
    amount: Money
    status: TransactionStatus


class InitiateDeposit:
    """Open a collection against a wallet, and record that it was asked for.

    **The mirror of ``WalletOperation``, not a variant of it.** That template
    writes the ledger row and then calls ``_apply`` - record, then move - and it
    can afford to because both balances it touches are inside this system. Here
    the thing that decides whether the money arrives is a person with a card, and
    crediting on intent would be creating money out of a promise. So the order
    inverts: record the intent, credit nothing, and wait. Forcing this through
    the template would mean a second flag beside ``settles_immediately`` whose
    two settings mean opposite things - "debited, row pending" against "not
    debited, row pending" - and one flag cannot say both without becoming a word
    that means nothing.

    **Two units of work, with the network call between them.** The first reads
    the wallet and the user and validates; then the provider is called; then a
    second unit writes the row. Holding one SQLite write transaction open across
    a third party's response time would block every other writer on this database
    for as long as that party felt like taking, which is a self-inflicted outage
    proportional to an outage elsewhere. The cost of splitting is a window in
    which two requests under one key both reach the provider - and that window is
    not closed by luck: the reference we send *is* the idempotency key, so the
    provider refuses the second one, and the ``UNIQUE`` on
    ``transactions.internal_reference`` refuses the second row.

    **A closed wallet is refused before the provider is called**, and the reason
    is worth following through. ``Wallet.apply_deposit`` refuses a CLOSED wallet,
    so a collection opened against one would take a payer's money that nothing
    could ever credit. Refusing at the door means the payer is never sent
    anywhere. A *frozen* wallet is allowed, deliberately: freezing stops value
    leaving, and this is value arriving.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        provider: PaymentProvider,
        *,
        actor: UUID,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._provider = provider
        # Required and keyword-only, exactly as ``WalletService`` takes it, and
        # for the same reason: every construction site has to answer the
        # question rather than inherit silence.
        self._actor = actor

    def execute(
        self, wallet_id: UUID, amount: Money, internal_reference: str
    ) -> InitiatedDeposit:
        """Ask the provider to collect ``amount`` into ``wallet_id``.

        The email the provider requires is read from the actor's own user record
        rather than taken as an argument, and that is a narrowing rather than a
        convenience. An ``email`` parameter would be a value the caller supplies
        alongside an actor id, free to disagree with it - and the one place this
        system hands a third party a fact about a person is not the place to
        accept an unverified one.
        """
        reference, email = self._prepare(wallet_id, amount, internal_reference)

        intent = self._provider.initialize_deposit(
            reference=reference, amount=amount, email=email
        )

        uow = self._unit_of_work_factory.start()
        try:
            transaction = Transaction(
                wallet_id=wallet_id,
                type=TransactionType.DEPOSIT,
                amount=amount,
                internal_reference=reference,
                # What the provider will call this movement when it reports back,
                # which is the only handle an arriving webhook can match on. It
                # equals ``reference`` today because we choose the provider's
                # reference ourselves; the two columns stay separate because a
                # provider that minted its own would need a place to say so, and
                # discovering that later would be a migration.
                provider_reference=intent.provider_reference,
            )
            uow.transactions.save(transaction)
        except BaseException:
            uow.rollback()
            raise
        else:
            uow.commit()

        return InitiatedDeposit(
            authorization_url=intent.authorization_url,
            provider_reference=intent.provider_reference,
            amount=amount,
            status=transaction.status,
        )

    def _prepare(
        self, wallet_id: UUID, amount: Money, internal_reference: str
    ) -> tuple[str, str]:
        """Validate against the wallet, and return the scoped key and the email.

        Everything that can be known without asking the provider happens here, in
        a unit that writes nothing. The dedupe check is part of it, and it is
        deliberately *before* the call rather than after: a retry that reached
        the provider would open a second collection under a reference it has
        already seen. The provider would refuse it - that is what the reference
        is for - but refusing it here means the request never leaves this
        process at all.

        The read-only unit is rolled back rather than committed. Nothing was
        written, so there is nothing to make durable; but the transaction is
        open, and leaving it open holds a read lock the next writer waits on.
        """
        uow = self._unit_of_work_factory.start()
        try:
            wallet = uow.wallets.get_owned(wallet_id, self._actor)
            user = uow.users.get_by_id(self._actor)

            if not isinstance(amount, Money):
                raise InvalidAmountError("amount must be a Money")

            if amount.amount <= 0:
                raise InvalidAmountError("amount must be greater than zero")

            if amount.currency != wallet.currency:
                raise CurrencyMismatchError("currency must be the same")

            # The order of the two checks above matters: the amount is checked
            # before the currency because a caller that sent something which is
            # not a Money at all would otherwise be told its currency was wrong.
            if wallet.status is WalletStatus.CLOSED:
                raise WalletClosedError("this wallet is closed")

            # The payer address, refused here when it plainly cannot work.
            # ``payerEmail`` carries the argument for the check's narrowness, and
            # the short version is that this is a courtesy and the provider is the
            # guard: the adapter raises this same error when Paystack refuses the
            # address, so a client is told one thing either way and nothing rests
            # on this codebase's reading of the provider's rule. It sits after the
            # wallet checks so that a closed wallet answers with its own refusal
            # rather than a lecture about an address.
            refuse_unusable_payer_email(user.email)

            # Which is ``app.domain.money.reference``'s rule rather than this
            # method's, and it moved there after the copy that used to stand here
            # turned out to be the one that reached the wire - carrying a
            # separator Paystack refuses. What this method owes the rule is the
            # wallet it has already proved the actor owns, and nothing else.
            reference = scoped_reference(wallet.wallet_id, internal_reference)
            if uow.transactions.get_by_internal_reference(reference) is not None:
                raise DepositAlreadyInitiatedError(
                    "a deposit is already open under this key"
                )
        finally:
            uow.rollback()

        return reference, user.email
