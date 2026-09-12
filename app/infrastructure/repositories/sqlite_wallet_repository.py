import sqlite3

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.fund import Fund
from app.domain.money.fundKind import FundKind
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.repositories.wallet_repository import WalletRepository
from app.infrastructure.persistence.serialization import (
    date_to_text,
    datetime_to_text,
    enum_to_text,
    money_to_text,
    text_to_date,
    text_to_datetime,
    text_to_enum,
    text_to_money,
    text_to_uuid,
    uuid_to_text,
)

_FUND_COLUMNS = (
    "fund_id, wallet_id, name, kind, balance, maturity_date, "
    "sealed_at, first_funded_at, created_at"
)


class SqliteWalletRepository(WalletRepository):
    """Wallet store over a single SQLite connection.

    The connection owns the transaction this repository participates in; save()
    only issues SQL and does not commit, so the Unit of Work decides when the
    write becomes durable.

    **A wallet's pots are written and read as part of the wallet.** They live in
    their own table, but not as an aggregate of their own: no code loads a pot
    without the wallet that holds it, because there is no question about a pot
    that the wallet is not part of the answer to. In particular the wallet's
    ``locked_balance`` is *defined* as the sum of these rows, so loading one
    without the other would produce a wallet that cannot say how much it holds.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, wallet: Wallet) -> Wallet:
        self._connection.execute(
            """
            INSERT INTO wallets
                (wallet_id, user_id, currency, status, available_balance)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(wallet_id) DO UPDATE SET
                user_id           = excluded.user_id,
                currency          = excluded.currency,
                status            = excluded.status,
                available_balance = excluded.available_balance
            """,
            (
                uuid_to_text(wallet.wallet_id),
                uuid_to_text(wallet.user_id),
                enum_to_text(wallet.currency),
                enum_to_text(wallet.status),
                money_to_text(wallet.available_balance),
            ),
        )
        self._sync_funds(wallet)
        return wallet

    def _sync_funds(self, wallet: Wallet) -> None:
        """Make the wallet's rows match the wallet's pots, by rewriting them.

        Delete-then-insert rather than a per-row upsert plus a sweep for the ones
        that vanished. The reason is the same one that made ``locked_balance`` a
        sum: **the stored rows should not be able to disagree with the aggregate
        in any way at all.** An upsert that forgets a pot, or a sweep that misses
        one, leaves rows that are *almost* right - and almost right is the state
        that produces "the pot says 5,000 but the wallet says 4,900". Rewriting
        makes the state after a save a function of the wallet alone, so there is
        no path that leaves a stale row behind.

        The cost is real and worth naming: every save rewrites every pot of that
        wallet, even the untouched ones. That is a handful of rows per wallet,
        written inside a transaction that is already open - the trade is a small
        amount of writing for the removal of a whole class of bug.
        """
        self._connection.execute(
            "DELETE FROM funds WHERE wallet_id = ?",
            (uuid_to_text(wallet.wallet_id),),
        )
        for fund in wallet.funds:
            self._connection.execute(
                f"""
                INSERT INTO funds ({_FUND_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid_to_text(fund.fund_id),
                    uuid_to_text(wallet.wallet_id),
                    fund.name,
                    enum_to_text(fund.kind),
                    money_to_text(fund.balance),
                    date_to_text(fund.maturity_date),
                    datetime_to_text(fund.sealed_at),
                    datetime_to_text(fund.first_funded_at),
                    datetime_to_text(fund.created_at),
                ),
            )

    def get_owned(self, wallet_id, user_id) -> Wallet:
        """Return this owner's wallet, or raise WalletNotFoundError.

        The owner sits in the ``WHERE`` rather than being checked once the row
        has come back, and that placement is the design rather than a style
        choice. A fetch-then-compare loads a stranger's wallet into memory before
        deciding not to hand it over - so the wrong wallet was briefly *read*,
        and the only thing standing between it and a caller is a comparison that
        some future path could forget. Filtering in SQL makes the wrong wallet
        something this method is unable to produce, whatever the caller does.

        "Not yours" and "not there" arrive at the same ``row is None`` and leave
        by the same raise, with no branch between them - so from outside the two
        are the same event. See the port for why that has to be true.
        """
        row = self._connection.execute(
            """
            SELECT wallet_id, user_id, currency, status, available_balance
            FROM wallets
            WHERE wallet_id = ? AND user_id = ?
            """,
            (uuid_to_text(wallet_id), uuid_to_text(user_id)),
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
            _funds=self._funds_for(row["wallet_id"], currency),
        )

    def _funds_for(self, wallet_id_text: str, currency: Currency) -> tuple[Fund, ...]:
        """Load a wallet's pots, in the order the aggregate holds them.

        **``ORDER BY rowid`` is deliberate, and it is a dependency on how
        ``_sync_funds`` writes rather than on anything SQLite promises about
        storage.** The order of ``Wallet.funds`` is load-bearing - it is the order
        a payout draws on pots when none is named - and ``created_at`` cannot
        reproduce it, because two pots opened with the same moment tie and SQLite
        is then free to return them either way. Tests that open two pots with one
        ``as_of`` hit that tie immediately.

        ``rowid`` does reproduce it, because ``_sync_funds`` deletes a wallet's
        rows and re-inserts them in ``wallet.funds`` order, so increasing rowid
        *is* that order. That is why this is not an arbitrary tiebreak: it is the
        same order the tuple had, recovered. It also means changing ``_sync_funds``
        to an upsert would silently reorder pots - so the coupling is stated here
        rather than left to be discovered.
        """
        rows = self._connection.execute(
            f"""
            SELECT {_FUND_COLUMNS}
            FROM funds
            WHERE wallet_id = ?
            ORDER BY rowid
            """,
            (wallet_id_text,),
        ).fetchall()
        return tuple(self._row_to_fund(row, currency) for row in rows)

    def _row_to_fund(self, row, currency: Currency) -> Fund:
        """Rebuild one pot, in the currency of the wallet it was loaded with.

        The currency is passed in rather than read from the row for the reason
        the funds table has no currency column: it belongs to the wallet, and the
        pot takes it from there.
        """
        return Fund(
            fund_id=text_to_uuid(row["fund_id"]),
            name=row["name"],
            kind=text_to_enum(FundKind, row["kind"]),
            _balance=text_to_money(row["balance"], currency),
            maturity_date=text_to_date(row["maturity_date"]),
            sealed_at=text_to_datetime(row["sealed_at"]),
            first_funded_at=text_to_datetime(row["first_funded_at"]),
            created_at=text_to_datetime(row["created_at"]),
        )
