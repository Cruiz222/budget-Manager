"""Command-line interface for Budget Manager.

The presentation layer's only job is to translate user intent into calls on the
application service and render the result. It touches one object - the
WalletService built by the composition root - and never imports repositories or
the Unit of Work.

Run from the repo root:

    .venv/bin/python -m app.presentation.cli --db budget.db open --currency NGN
    .venv/bin/python -m app.presentation.cli --db budget.db deposit <wallet_id> 5000
"""

import argparse
import sys
import uuid
from decimal import Decimal, InvalidOperation

from app.application.wallet_service import WalletService
from app.composition_root import build_wallet_service
from app.domain.money.currency import Currency
from app.domain.money.exception import MoneyError
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

#: Which operations move money, and the verb to report on success.
OPERATIONS = {
    "deposit": "deposited",
    "withdraw": "withdrew",
    "lock": "locked",
    "release": "released",
}


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid UUID: {value!r}")


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"invalid amount: {value!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="budget-manager",
        description="A personal savings wallet with deposit, withdraw, "
        "lock and release operations.",
    )
    parser.add_argument(
        "--db",
        default="budget.db",
        help="SQLite database file (default: budget.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open", help="open a new wallet")
    open_parser.add_argument(
        "--user",
        type=_uuid,
        help="owner user UUID (auto-generated if omitted)",
    )
    open_parser.add_argument(
        "--currency",
        required=True,
        choices=[currency.name for currency in Currency],
        help="wallet currency",
    )

    balance_parser = subparsers.add_parser(
        "balance", help="show a wallet's status and balances"
    )
    balance_parser.add_argument("wallet_id", type=_uuid)

    freeze_parser = subparsers.add_parser(
        "freeze", help="freeze a wallet (stops withdrawals)"
    )
    freeze_parser.add_argument("wallet_id", type=_uuid)

    unfreeze_parser = subparsers.add_parser(
        "unfreeze", help="return a frozen wallet to active"
    )
    unfreeze_parser.add_argument("wallet_id", type=_uuid)

    history_parser = subparsers.add_parser(
        "history", help="show a wallet's transaction ledger, oldest first"
    )
    history_parser.add_argument("wallet_id", type=_uuid)

    for name, _ in OPERATIONS.items():
        op_parser = subparsers.add_parser(name, help=f"{name} money")
        op_parser.add_argument("wallet_id", type=_uuid)
        op_parser.add_argument("amount", type=_decimal)
        op_parser.add_argument(
            "--ref",
            help="idempotency key (auto-generated if omitted)",
        )

    return parser


def _open(service: WalletService, args) -> int:
    user_id = args.user if args.user is not None else uuid.uuid4()
    wallet = service.open_wallet(user_id, Currency[args.currency])
    print(
        f"opened wallet {wallet.wallet_id} "
        f"(currency {wallet.currency.name})"
    )
    return 0


def _balance(service: WalletService, args) -> int:
    wallet: Wallet = service.get_wallet(args.wallet_id)
    print(f"status: {wallet.status.name.lower()}")
    print(f"available: {wallet.available_balance}")
    print(f"locked: {wallet.locked_balance}")
    return 0


def _operation(service: WalletService, args) -> int:
    # Resolve the wallet so the unitless amount is interpreted in its currency.
    wallet = service.get_wallet(args.wallet_id)
    amount = Money(args.amount, wallet.currency)
    internal_reference = args.ref if args.ref is not None else str(uuid.uuid4())

    method = getattr(service, args.command)
    method(args.wallet_id, amount, internal_reference)

    current = service.get_wallet(args.wallet_id)
    verb = OPERATIONS[args.command]
    print(
        f"{verb} {amount} | "
        f"available {current.available_balance} | "
        f"locked {current.locked_balance}"
    )
    return 0


def _freeze(service: WalletService, args) -> int:
    wallet = service.freeze_wallet(args.wallet_id)
    print(f"wallet {wallet.wallet_id} is now {wallet.status.name.lower()}")
    return 0


def _unfreeze(service: WalletService, args) -> int:
    wallet = service.unfreeze_wallet(args.wallet_id)
    print(f"wallet {wallet.wallet_id} is now {wallet.status.name.lower()}")
    return 0


def _history(service: WalletService, args) -> int:
    transactions = service.transactions_for_wallet(args.wallet_id)
    if not transactions:
        print(f"no transactions for wallet {args.wallet_id}")
        return 0
    for transaction in transactions:
        print(
            f"{transaction.created_at.isoformat(timespec='seconds')}  "
            f"{transaction.type.name.lower():<8} "
            f"{transaction.amount}  "
            f"{transaction.status.name.lower()}"
        )
    return 0


def _describe(exc: MoneyError) -> str:
    return str(exc) if str(exc) else exc.__class__.__name__


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = build_wallet_service(
        unit_of_work_factory=SqliteUnitOfWorkFactory(args.db)
    )
    try:
        if args.command == "open":
            return _open(service, args)
        if args.command == "balance":
            return _balance(service, args)
        if args.command == "freeze":
            return _freeze(service, args)
        if args.command == "unfreeze":
            return _unfreeze(service, args)
        if args.command == "history":
            return _history(service, args)
        return _operation(service, args)
    except MoneyError as exc:
        print(f"error: {_describe(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
