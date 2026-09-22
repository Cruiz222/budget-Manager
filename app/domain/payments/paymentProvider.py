from abc import ABC, abstractmethod

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.money import Money
from app.domain.payments.paymentIntent import PaymentIntent
from app.domain.payments.transferIntent import TransferIntent
from app.domain.payments.providerAnswer import ProviderAnswer


class PaymentProvider(ABC):
    """What the application tells a payment provider to do - the *outbound port*.

    Everything else in ``app.domain.repositories`` describes storage, and this
    looks like it belongs there until you notice the direction. A repository is
    something the application *asks* for data; this is something the application
    *tells* to act. It is the same seam ``NotificationChannel`` is, on the other
    side of the money: that one carries words out, this one carries a collection
    request out. Both are abstract for the same reason - so the inside never
    names a technology - and both are why no test in this suite opens a socket.

    **Four methods, and the port grew one at a time rather than starting
    there.** The obvious shape is a symmetric pair - collect money, send money -
    and only the first half of that pair has a caller: payouts reach a provider
    through a path that does not exist yet, so an ``initiate_transfer`` now would
    be a method whose signature was guessed rather than derived. The second
    method is the *other direction of the same conversation* rather than the
    other half of that pair - a provider that signs its messages has to be able
    to prove one, and only the provider knows how - and it had a caller from the
    day it was written. ``NotificationChannel`` has one method for the same
    reason, and it has been enough for two message kinds.

    **The third method is the first one that asks.** The first two *tell* the
    provider to do something; ``outcome_for`` asks it a question. It arrived last
    for the discipline's own reason rather than by accident: reconciliation is
    the caller, and before reconciliation existed a signature here would have
    been guessed - which is exactly the mistake that keeps ``initiate_transfer``
    off this port. It sits in the outbound half, because it shares a failure mode
    and a return direction with ``initialize_deposit`` - the fourth method reads
    between the two, for its own reason below - while ``verify_signature`` at the
    end is a method that answers instead of acting.

    **The fourth is the one that says what cannot be asked for, and it earns its
    place by the same test the third did.** ``supported_currencies`` has a caller
    that exists today - the deposit use case, which has to refuse a wallet the
    rail cannot collect *before* it sends a payer to a payment page - and a
    signature derived from that caller rather than guessed at, which is what
    separates it from ``initiate_transfer``. It is also the reason the port has
    to carry it at all: the fact belongs to the adapter, and an application layer
    that learned it any other way would have to hold a second copy of a fact that
    the adapter is the only thing able to honour. **A copy is a thing that
    drifts**, and the direction it drifts in is the one where the door admits a
    currency the transport will silently relabel.

    **The inbound half of settlement is deliberately not on the webhook path.**
    There, deciding what a ``charge.success`` *means* needs no call outward: the
    provider tells us, and what it says arrives as a ``ProviderOutcome``. A port
    is what we say; that is what we hear. What is here about hearing is only the
    one thing that is genuinely the provider's business - whether the message was
    theirs.

    **A message that never arrives cannot be heard**, though, and no amount of
    parsing fixes that. So the port also carries the outbound form of the same
    question - "what happened to this reference?" - which is a call we make
    rather than one we receive. That is not a contradiction of the paragraph
    above; it is what the paragraph above leaves unanswerable.

    **Raising is the interface, not an error path.** ``initialize_deposit`` is
    expected to raise ``PaymentProviderError`` when it cannot do its job, and the
    caller treats that as ordinary: the deposit request is refused and the client
    is told so. A provider that returned a sentinel on failure would leave the
    caller unable to tell "here is your link" from "there is no link", and the
    one that guessed wrong would hand a user an authorization URL that does not
    exist. The reasoning is ``NotificationChannel``'s exactly. ``outcome_for``
    raises for the same end one step removed: it has a perfectly good answer for
    every state a *payment* can be in - including the uncomfortable ones - so the
    only thing left to raise about is a call that did not complete, and a caller
    that received a sentinel instead would have no way to tell "nothing has
    settled" from "I could not ask".

    ``verify_signature`` is the exception to that, and it is exempt for a reason
    worth stating so the two do not read as inconsistent: it answers a yes/no
    question that has a perfectly good "no". A wrong signature is not a failure
    of anything - it is the expected outcome of a stranger calling - so there is
    nothing to raise about, and a port that raised here would make the route
    catch an exception to decide a status code it already knows.
    """

    @abstractmethod
    def initialize_deposit(
        self, *, reference: str, amount: Money, email: str
    ) -> PaymentIntent:
        """Ask the provider to collect ``amount``, and get back where to send them.

        ``reference`` is ours, not the provider's, and it is the whole of the
        idempotency story. It is passed as the provider's own idempotency key, so
        a retry that reaches the provider twice is refused at *its* end as well
        as ours - which closes the window our local dedupe cannot, where two
        concurrent requests under one client key both get past the duplicate
        check and each open a collection. Every provider worth the name offers
        this, and a port that did not take a reference would make the guarantee
        unimplementable rather than merely absent.

        ``email`` is required by the provider and is not ours to invent. Paystack
        will not initialize a transaction without an address to attach it to -
        it is where the receipt goes - so the payer's address travels here from
        the actor the request authenticated as. Note that this is the one place
        the API hands a third party a fact about a person, and it is the minimum
        such fact rather than a convenience: nothing else about the actor goes
        out, and the wallet id is already inside ``reference`` because that is
        what makes it unique.

        Raising means no collection was opened and the caller must not record
        one. Returning means the provider has taken responsibility for it, and
        the reference in the answer is the name it will report it under.
        """
        pass

    @abstractmethod
    def supported_currencies(self) -> frozenset[Currency]:
        """The currencies this provider will actually collect in.

        **Asked before ``initialize_deposit``, never after.** A collection is
        opened by sending a payer somewhere, and the one thing this call exists
        to prevent is sending them somewhere for money this rail cannot take in
        the currency the wallet holds. A caller that asked afterwards would have
        the answer at the only moment it is no longer any use.

        **A fact about the deployment, not about the port.** A provider account
        is enabled for particular currencies by whoever opened it, so what this
        returns is a statement about an account rather than about Paystack - and
        it is deliberately allowed to differ between two providers of the same
        kind. Implementations return a ``frozenset`` so that the answer cannot be
        edited in place by a caller that was only asking.

        **Implementations must derive it from whatever they send.** The
        requirement this signature cannot enforce, and therefore has to write
        down, is the one a real incident produced: an adapter that reported one
        set of currencies here and put a *constant* currency in the payload built
        collections whose number and label disagreed, and the payer's money was
        taken against a ledger row that could never settle. An implementation
        that sends ``amount.currency`` and answers this from the same place
        cannot have that bug. See ``initiate_deposit``, which is the caller.
        """
        pass

    @abstractmethod
    def outcome_for(self, reference: str) -> ProviderAnswer:
        """Ask the provider what became of the collection named by ``reference``.

        **The method a webhook cannot replace.** Everything else in settlement is
        push - the provider tells us, and we believe it. A push that never
        happens leaves a row PENDING forever with the payer's money already gone
        from their account, and there is no local fact that distinguishes it from
        a payment still in progress. Only the provider knows, so somebody has to
        ask.

        **It takes a reference and returns an answer, and the answer is never a
        boolean.** "Has this settled?" would be a lie by omission: a provider that
        has never heard of a reference would answer "no", which is the same
        answer as a payer who has not paid yet and an entirely different
        operational fact. ``ProviderAnswerStatus`` carries all three, and the
        caller is expected to treat ``NO_SUCH_REFERENCE`` as the alarm it is
        rather than as a slower kind of pending.

        **Not settled is a perfectly good answer, so it is not raised.** A
        provider is asked about rows that are simply slow - that is most of what
        it will say - and a refusal here would make the ordinary case the
        exceptional one. Raising is reserved for a call that could not be made or
        could not be understood; see the class docstring.

        Implementations must not decide anything about the money. This returns
        what the provider said, translated into our vocabulary and no further:
        whether an answer means a wallet should be credited is
        ``SettlePayment``'s question, asked of the same ``ProviderOutcome`` path
        a webhook uses, so that a recovered payment and a delivered one cannot
        diverge.
        """
        pass

    @abstractmethod
    def verify_signature(self, body: bytes, signature: str | None) -> bool:
        """Whether this body was sent by the provider this installation trusts.

        **The bytes, not a parsed model.** A signature is computed over a byte
        string, so verifying one against anything re-serialized from it is
        verifying a different byte string - and a difference of one space would
        present as a wrong secret key, which is a diagnosis that sends somebody
        looking in exactly the wrong place. The parameter is ``bytes`` because
        that is what the guarantee is about.

        ``signature`` is ``str | None`` rather than ``str`` because a header can
        be absent, and "absent" and "wrong" are the same answer here. Making the
        caller check for the header first would be making every caller write the
        same two lines, and the second caller would eventually write them
        differently.

        Returns a ``bool`` instead of raising, unusually for this port - see the
        class docstring for why a rejection is a perfectly good answer rather
        than an error path.

        **Implementations must compare in constant time.** A byte-by-byte
        comparison that stops at the first difference leaks how much of a guessed
        signature was correct, which turns forging one from infeasible into
        arithmetic. That is a requirement on the implementation rather than
        something this signature can enforce, which is exactly why it is written
        here where an implementer will read it.
        """
        pass

    @abstractmethod
    def initiate_transfer(
        self,
        *,
        reference: str,
        amount: Money,
        destination: Destination,
    ) -> TransferIntent:
        """Ask the provider to initiate an external money transfer."""
        pass
