from dataclasses import dataclass
from uuid import UUID

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.tier import check_credit, tier_for
from app.domain.money.currency import Currency
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
from app.domain.payments.exception import (
    CurrencyNotCollectableError,
    DepositAlreadyInitiatedError,
    PayerEmailMissingError,
)
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

    **A wallet in a currency this installation cannot collect is refused at the
    same door, for the same reason, and it is the newer of the two.** The rail is
    enabled for particular currencies and ``Currency`` has five members, so a
    collection opened for any of the other four would be taken at the far end in
    the wrong one - and because ``SettlePayment`` compares what arrived against
    what the row asked for, the disagreement would surface as a row that can
    never settle, *after* the payer had paid. The check is
    ``PaymentProvider.supported_currencies`` asked of the provider rather than a
    constant held here, so the set has one home: the adapter that has to honour
    it. **This is not a restriction on opening a wallet** - a wallet is a
    container, and one holding US dollars holds pots and plans and can be
    credited directly - it is a restriction on one door, and it is recorded in
    the README as a departure from where the checklist first put it.

    **The balance ceiling is refused here too, and for the same reason as the
    closed wallet rather than a different one.** A deposit that would leave the
    wallet above its cap cannot be credited when it settles, so opening a
    collection for it sends a payer to a payment page to buy a refusal. The check
    is ``check_credit`` itself rather than a second comparison written out, so the
    courtesy and the rule it foreshadows cannot drift apart; what differs between
    them is only *when* they run, and the balance is free to change in between.
    That gap is a real one and it is **not** closed here - see ``_prepare``.
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

        **Every refusal here costs nobody anything, and that is the thread
        through all of them.** The payer has not been sent anywhere yet - the
        provider is called by ``execute`` after this returns - so a wallet that
        is closed, a currency this installation cannot collect, a balance that
        would breach the account's cap and an address the provider will not bill
        are all answered while the money is still entirely on the payer's side of
        the page. That is what makes this method the right place for checks that
        look like they belong further in: the aggregate would refuse them too,
        but by then a collection exists.

        **The ceiling check is a courtesy, and it is no longer nothing but a
        courtesy.** What it buys is that a payer is not sent to a payment page
        for money this wallet could not legally accept. It used to buy only that,
        and the gap behind it was the one this paragraph then described: two
        collections opened in the same moment, each of which fit under the cap as
        it was read, both being paid and both settling, with nothing refusing the
        second because ``SettlePayment`` credits the wallet directly and consults
        no ceiling. The cap was therefore bounded by *when* a client asked rather
        than by what the account may hold, on the one ceiling whose argument is
        anti-mule rather than convenience.

        **Counting the money in flight is what closed it**, and it closed it on
        this side of the payment page rather than the other - which is the whole
        of why the fix is here. ``pending_credit_total`` answers what this wallet
        is already expecting, and adding it to the projected balance means the
        second of two collections is refused while the first is still open, so no
        payer is ever taken to a page for money that would breach the ceiling.
        That is this method's own thread being extended rather than a new policy:
        *every refusal here costs nobody anything*, and refusing here is the only
        place where that is true of this particular breach.

        **What is still open, and stated rather than left to be discovered.**
        Two requests that read *simultaneously* can still both pass, because the
        ``PENDING`` row is written after ``initialize_deposit`` returns and not
        before - deliberately, since holding a write transaction across a third
        party's response time is an outage proportional to theirs, and a row
        written optimistically is a row no webhook may ever settle. Closing that
        window means reserving at intent time, which reverses a decision this
        slice is not the place to reverse. So the residual is real, and the
        honest handling of it is on the settlement side rather than here: a
        credit that breaches the ceiling *is* applied - the payer's money is real
        and there is no rail to send it back, ``PaymentProvider`` having no
        refund method - and ``SettlePayment`` records it loudly so an operator
        can see the control was crossed. Accepting silently was the one answer
        that was not available.
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

            # **The currency guard, and the reason it is here rather than at
            # wallet creation.** What refuses this request is the meeting of a
            # wallet's currency and a *rail*: this installation collects in one
            # currency, a wallet may be opened in any of five, and a collection
            # opened for the other four would be a collection the far end takes
            # in naira against a ledger row that says dollars. Both halves of
            # that are already here - the wallet by ``get_owned`` above and the
            # rail by ``self._provider`` - so the check costs a set member and
            # no read at all.
            #
            # It sits after the wallet's own status and after the amount's
            # currency, and each edge is load-bearing. After ``CLOSED`` because
            # a closed wallet is the refusal with a remedy - give up on this one
            # - while this one has none, and answering the actionable fact first
            # is the same ordering ``errors`` uses to put 401 above 404. After
            # ``amount.currency != wallet.currency`` because that check has its
            # own sentence: a US dollar amount sent to a naira wallet is a
            # *mismatch*, which is the caller's typo, and would be told the
            # wrong thing if this caught it first.
            #
            # **It is not a rule about ``Wallet``, which is why it is not in
            # ``Wallet``.** A wallet holding USD is a perfectly good wallet: it
            # is a container, it holds pots and plans, and ``WalletService`` can
            # credit it directly without any rail being involved. What is
            # impossible is a *collection* for it, and that is a fact about this
            # door. Putting the rule in the aggregate would say the domain knew
            # what a payment provider can do, and it does not.
            #
            # Asked of the provider rather than of a constant here, so that
            # "which currencies can this account collect" has one home - the
            # adapter that has to honour it. See
            # ``PaymentProvider.supported_currencies``.
            collectable = self._provider.supported_currencies()
            if wallet.currency not in collectable:
                raise CurrencyNotCollectableError(
                    f"this wallet holds {wallet.currency.value}, and this "
                    f"installation can only collect deposits in "
                    f"{_sorted_names(collectable)}; the wallet is still usable "
                    f"for everything on this side of the rail"
                )

            # **The dedupe check comes before the ceiling, and the order is the
            # fix rather than a detail.** A retry under a key that already names
            # a collection is not a new request: it is the same request arriving
            # twice, and the answer it is owed is "you already asked for this" -
            # ``DepositAlreadyInitiatedError``, which names the one thing the
            # caller can do about it. The ceiling below cannot answer that. It
            # reads every PENDING row as money on its way in, so a retry of a
            # collection that is still open counts *its own* amount a second
            # time, and a request whose first call fit can be refused on the
            # retry for a breach that exists only because the retry was counted.
            # "This account may not hold that much" would send a client who asked
            # once off to fix the wrong thing - and a client retrying after a
            # lost response is the ordinary case this door is designed for, not a
            # misuse. So the question about the request is asked before the
            # question about the account, which is the same order ``errors`` uses
            # to put 401 above 404.
            #
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

            # Read through the actor rather than through the wallet, and the
            # distinction is the same one ``get_owned`` above makes: the wallet
            # has just been proved to belong to ``self._actor``, so the profile
            # that answers here is the owner's. A missing one is not an error -
            # ``tier_for`` reads ``None`` as UNVERIFIED, which is what every
            # account that predates profiles is.
            #
            # The total the wallet would *hold* rather than the available balance
            # alone, because that is what the credit guard compares: a pot is
            # money this account still holds, so a deposit into a wallet whose
            # locked balance already reaches the cap is refused here as surely as
            # it would be refused later.
            #
            # **And what is already in flight counts, which is the half this
            # check used to be missing.** Every collection opened but not yet
            # settled is money this wallet is about to hold, and the read below
            # is the incoming mirror of the one behind the daily outflow cap -
            # which counts ``PENDING`` rows for exactly this reason, in the port's
            # own words: "a cap that ignored held money would let a burst of
            # in-flight payouts each pass on its own." Without it, the same
            # sentence was true of deposits: N collections opened a moment apart
            # each read the same untouched balance, each fit under the ceiling,
            # and all of them settled - so the balance cap was bounded by nothing
            # an attacker could not exceed by asking twice. The cap is the
            # AML-shaped ceiling ("value accumulating in an account nobody has
            # identified"), so passing it by concurrency is passing the control
            # that matters most.
            open_deposits = uow.transactions.pending_credit_total(
                wallet.wallet_id, wallet.currency
            )
            check_credit(
                tier_for(uow.profiles.find_for_user(self._actor)),
                wallet.available_balance
                + wallet.locked_balance
                + open_deposits
                + amount,
            )

            # The payer address, refused here when it plainly cannot work.
            # ``payerEmail`` carries the argument for the check's narrowness, and
            # the short version is that this is a courtesy and the provider is the
            # guard: the adapter raises this same error when Paystack refuses the
            # address, so a client is told one thing either way and nothing rests
            # on this codebase's reading of the provider's rule. It sits after the
            # wallet checks so that a closed wallet answers with its own refusal
            # rather than a lecture about an address - and after the ceiling for
            # the same reason, since "this account may not hold that much" is a
            # fact about the account and an unusable address is a courtesy about
            # the request.
            #
            # The missing case comes *first*, and the order is not cosmetic: the
            # courtesy below asks a question about an address, and there is no
            # address here to ask it about - ``has_real_domain(None)`` is an
            # ``AttributeError``, which reports as a 500 rather than as a refusal.
            # A phone-only account is a state this system permits and a person can
            # therefore be in, so the branch is reachable rather than theoretical.
            # See ``PayerEmailMissingError``, which names the remedy.
            if user.email is None:
                raise PayerEmailMissingError(
                    "this account has no email address, and a deposit needs one to "
                    "be billed under; add an email to the account and try again"
                )

            refuse_unusable_payer_email(user.email)
        finally:
            uow.rollback()

        return reference, user.email


def _sorted_names(currencies: frozenset[Currency]) -> str:
    """A set of currencies as a readable list, for the sentence a caller reads.

    Sorted, and that is the whole of why it is a function rather than an f-string
    at the raise site. A ``frozenset`` has no order, so a message built by
    iterating one names the same currencies in a different order on different
    runs - and a sentence that changes while nothing has changed is one a reader
    learns to distrust, which is the last thing a refusal wants. Sorted by the
    currency's own code rather than by insertion, so it does not depend on how
    the set was built either.

    Local rather than shared with the adapter's own version of this line, and the
    duplication is deliberate: this layer may not import from
    ``app.infrastructure``, and the alternative - a domain helper that exists to
    format a message - would put prose in the one layer that has none.
    """
    return ", ".join(sorted(currency.value for currency in currencies))
