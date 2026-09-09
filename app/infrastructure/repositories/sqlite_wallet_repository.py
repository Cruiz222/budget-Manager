import sqlite3

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.repositories.wallet_repository import WalletRepository
from app.infrastructure.persistence.serialization import (
    enum_to_text,
    money_to_text,
    text_to_enum,
    text_to_money,
    text_to_uuid,
    uuid_to_text,
)


class SqliteWalletRepository(WalletRepository):
    """Wallet store over a single SQLite connection.

    The connection owns the transaction this repository participates in; save()
    only issues SQL and does not commit, so the Unit of Work decides when the
    write becomes durable.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, wallet: Wallet) -> Wallet:
        self._connection.execute(
            """
            INSERT INTO wallets
                (wallet_id, user_id, currency, status,
                 available_balance, locked_balance)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_id) DO UPDATE SET
                user_id           = excluded.user_id,
                currency          = excluded.currency,
                status            = excluded.status,
                available_balance = excluded.available_balance,
                locked_balance    = excluded.locked_balance
            """,
            (
                uuid_to_text(wallet.wallet_id),
                uuid_to_text(wallet.user_id),
                enum_to_text(wallet.currency),
                enum_to_text(wallet.status),
                money_to_text(wallet.available_balance),
                money_to_text(wallet.locked_balance),
            ),
        )
        return wallet

    def get_by_id(self, wallet_id) -> Wallet:
        row = self._connection.execute(
            """
            SELECT wallet_id, user_id, currency, status,
                   available_balance, locked_balance
            FROM wallets
            WHERE wallet_id = ?
            """,
            (uuid_to_text(wallet_id),),
        ).fetchone()
        if row is None:
            raise WalletNotFoundError
        return self._row_to_wallet(row)

    def _row_to_wallet(self, row) -> Wallet:
        currency = text_to_enum(Currency, row["currency"])
        return Wallet(
            wallet_id=text_to_uuid(row["wallet_id"]),
            user_id=text_to_uuid(row["user_id"]),
            currency=currency,
            status=text_to_enum(WalletStatus, row["status"]),
            _available_balance=text_to_money(row["available_balance"], currency),
            _locked_balance=text_to_money(row["locked_balance"], currency),
        )
