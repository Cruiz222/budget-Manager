from abc import ABC, abstractmethod

from app.domain.money.wallet import Wallet


class WalletRepository(ABC):
    """Defines what a wallet store must do (the domain does not care how)."""

    @abstractmethod
    def save(self, wallet: Wallet) -> Wallet:
        pass

    @abstractmethod
    def get_owned(self, wallet_id, user_id) -> Wallet:
        """Return this owner's wallet, or raise WalletNotFoundError.

        **There is no unscoped read of a wallet anywhere in this codebase, and
        that is the design rather than a convention.** ``get_by_id`` - which took
        only a wallet id and happily returned anybody's - was removed rather than
        deprecated, because a method with an owner-shaped hole in its signature
        is a method somebody eventually calls. The port is the enforcement: with
        no such method to reach for, "read someone else's wallet" is not a
        mistake that can be made, only one that would have to be written.

        **A wallet belonging to somebody else is reported exactly as a wallet
        that does not exist.** Same error, same message, no branch between them.
        The distinction is not the caller's to make: telling a stranger "that
        wallet exists, it just isn't yours" answers a question they have no
        standing to ask, and turns a guessable id into a way to enumerate who
        banks here.

        Absence is still unexpected in the ordinary case (a caller has a real
        wallet id in hand), which is why this raises rather than returning None -
        the same split as before, now with a second way to arrive at it.
        """
        pass

    @abstractmethod
    def list_for_owner(self, user_id) -> list[Wallet]:
        """Every wallet this owner holds, oldest first.

        **It takes an owner id, and that is the whole of its defence.** The
        method ``get_by_id`` was removed for - and the paragraph in ``get_owned``
        is about - a signature with an *owner-shaped hole* in it: a lookup that
        named a wallet and not a person, so that whoever called it got whatever
        it found. This names the person and not the wallet, which is the same
        rule read in the other direction. There is no argument to this method
        that a caller could fill in with somebody else's wallet, because there is
        no wallet argument at all.

        **An empty list is an answer, unlike ``get_owned``'s raise.** That
        method is asked about one wallet a caller already has an id for, so
        finding nothing is a contradiction worth refusing; this is asked by
        somebody who does not know what they hold, so "nothing" is the ordinary
        state of a person who has not opened a wallet yet. The asymmetry is the
        same one ``WalletNotEmptyError`` and an empty ledger have: absence is
        only a refusal when the question presupposed a presence.

        **The caller is a person looking at their own account.** Its arrival is
        what closes the last gap in the browser client - ``WalletService`` had
        ``get_wallet`` and ``transactions_for_wallet``, both of which demand a
        wallet id, and a person who has never been told one cannot begin. It is
        equally owed to the JSON API, which has the same hole for the same
        reason, and which is why it is a route as well as a port method.
        """
        pass

    @abstractmethod
    def owner_of(self, wallet_id) -> object:
        """Return the id of the user who owns this wallet, or raise.

        **This is the narrowest read that answers a question none of the others
        can, and it is deliberately narrower than it looks like it should be.**
        It returns a ``UUID`` - one column of one row - and never a ``Wallet``:
        no balance, no status, no pots, nothing that could be spent or reported.
        The caller is a webhook, which arrives holding a provider reference and
        knowing nothing about people, and which must then load the wallet *as its
        owner* through ``get_owned``. That second call is the one that reads the
        money, and it still takes an owner id, so the rule this port exists to
        keep is kept.

        **It is not decision 54's ``get_by_id`` coming back.** That method
        returned the wallet - the balances, the status, everything a caller would
        want - and it was removed because a signature with an owner-shaped hole
        in it is one somebody eventually fills with the wrong thing. This returns
        an identifier, and an identifier is not an asset. The honest comparison
        is ``TransactionRepository.get_by_provider_reference``, which is likewise
        a global lookup the settle path needs and likewise unnamed by any owner;
        and ``list_by_status``, which decision 57 accepts as a privileged read
        held in place by convention because no owner-scoped version of the
        question exists.

        **A second caller of this is a hole if it is user-reachable.** The one
        caller is ``SettlePayment``, reached from one route, behind an HMAC
        signature - not behind a token, because a provider has no token. There is
        no actor anywhere on that path, and this method is the single point where
        that is visible. Anything new that reaches for it must answer the same
        question that path answers: *who is allowed to ask this?* If the answer
        is a user, this is the wrong method and the wallet should have been loaded
        through ``get_owned`` from the start.

        A wallet that does not exist raises ``WalletNotFoundError``, the same
        error ``get_owned`` raises, so that a caller reaching a wallet through
        this method and one reaching it through the other cannot tell absence
        apart in two different ways.
        """
        pass
