from uuid import UUID

from fastapi import APIRouter, Depends

from app.application.wallet_service import WalletService
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import wallet_service

router = APIRouter(tags=["wallets"])


@router.post("/wallets", response_model=schemas.WalletOut, status_code=201)
def open_wallet(
    payload: schemas.OpenWalletIn,
    service: WalletService = Depends(wallet_service),
) -> schemas.WalletOut:
    """Open a wallet for the caller.

    **The owner is not in the request, and cannot be.** The service was built for
    one user before this function ran, so there is no field through which a
    caller could name somebody else - which is why the request body is a currency
    and nothing more. The CLI has the same shape for the same reason; see
    ``_open``.

    201 rather than 200, because this creates a resource and says where it is.
    """
    wallet = service.open_wallet(translate.currency_in(payload.currency))
    return translate.wallet_out(wallet)


@router.get("/wallets/{wallet_id}", response_model=schemas.WalletOut)
def get_wallet(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> schemas.WalletOut:
    """One wallet, with its pots.

    **Someone else's wallet is a 404 with the same body as one that does not
    exist**, and that is the whole of Phase 1a reaching the client. There is no
    branch here that could tell the two apart: the service's scoped read raises
    the same ``WalletNotFoundError`` for both, and ``errors`` renders it without
    inventing a distinction. See ``test_isolation.py``.

    The pots travel with the wallet rather than behind a second request because a
    wallet's balance is not readable without them: the locked balance is a sum
    over pots, so a client that had the wallet and not the pots would hold two
    numbers it could not reconcile.
    """
    return translate.wallet_out(service.get_wallet(wallet_id))


@router.get(
    "/wallets/{wallet_id}/transactions",
    response_model=list[schemas.TransactionOut],
)
def list_transactions(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> list[schemas.TransactionOut]:
    """A wallet's ledger, oldest first.

    The wallet is validated before the ledger is read, so "no such wallet" and
    "a wallet with no movements yet" are different answers - a 404 and an empty
    list. The distinction is not cosmetic: one means the caller has the wrong id,
    the other means the account is new, and a client that could not tell them
    apart would show "no transactions" to somebody who had mistyped a UUID.
    """
    return [
        translate.transaction_out(one)
        for one in service.transactions_for_wallet(wallet_id)
    ]
