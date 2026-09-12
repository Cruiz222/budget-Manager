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
