from app.domain.money.exception import WalletNotFoundError
from app.domain.repositories.wallet_repository import WalletRepository


class InMemoryWalletRepository(WalletRepository):
    def __init__(self):
        self.wallets = {}

    def save(self, wallet):
        self.wallets[wallet.wallet_id] = wallet
        return wallet

    def get_by_id(self, wallet_id):
        if wallet_id not in self.wallets:
            raise WalletNotFoundError

        return self.wallets[wallet_id]
