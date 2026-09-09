from abc import ABC, abstractmethod

from app.domain.money.wallet import Wallet


class WalletRepository(ABC):
    """Defines what a wallet store must do (the domain does not care how)."""

    @abstractmethod
    def save(self, wallet: Wallet) -> Wallet:
        pass

    @abstractmethod
    def get_by_id(self, wallet_id) -> Wallet:
        """Return the wallet with this id.

        Absence is unexpected here (a caller has a real wallet_id in hand), so
        it raises WalletNotFoundError instead of returning None.
        """
        pass
