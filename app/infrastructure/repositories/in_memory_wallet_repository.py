from app.domain.money.exception import WalletNotFoundError
from app.domain.repositories.wallet_repository import WalletRepository


class InMemoryWalletRepository(WalletRepository):
    """A dict-backed wallet store, for tests that do not need SQL.

    It implements the same port as the SQLite adapter and so carries the same
    ownership rule: a wallet is found by owner as well as by id, and failing
    either test is the *same* failure. A fake that kept an owner-less lookup
    would let a scoping bug pass in a test that never touches SQLite - and a
    fake is exactly where such a bug is most likely to be believed.
    """

    def __init__(self):
        self.wallets = {}

    def save(self, wallet):
        self.wallets[wallet.wallet_id] = wallet
        return wallet

    def get_owned(self, wallet_id, user_id):
        wallet = self.wallets.get(wallet_id)

        # One expression, not two raises: "no such wallet" and "not this
        # caller's wallet" are the same outcome by design, and spelling them
        # separately here would invite a future branch that distinguished them.
        if wallet is None or wallet.user_id != user_id:
            raise WalletNotFoundError

        return wallet
