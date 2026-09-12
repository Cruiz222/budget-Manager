"""Answering a recorded request to move money out, and reading one back.

Two routes, and they are the whole of the second half of the confirmation
feature. The first half is in ``wallets``: three money routes that used to move
money and now record a request to. This module is where those requests are
answered.

**A separate module from ``wallets``, and the paths say why.** A confirmation is
not part of a wallet - it is a decision about one, made later, and it is
addressed by its own id rather than by the wallet it names. Nesting the confirm
route under ``/wallets/{id}/...`` would have made it look like a fourth thing
you do to a wallet, when it is the second half of the three things already
there. Keeping the paths flat is what makes "what can I do to a wallet?" and
"what can I do to a request?" two readable lists instead of one mixed one.

**Both routes re-read the clock rather than trusting the request's.** The window
is judged by the server's moment, at the server's moment - a client cannot send
one, and there is no field for it. That is the same decision as ``expires_at``
being a moment in the response rather than a duration: the window belongs to the
row and to the clock that wrote it, and a caller holding a different idea of the
time must not be able to widen it.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends

from app.application.wallet_service import WalletService
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import wallet_service

router = APIRouter(tags=["confirmations"])


@router.post(
    "/confirmations/{confirmation_id}/confirm",
    response_model=schemas.ConfirmedOperationOut,
    status_code=201,
)
def confirm(
    confirmation_id: UUID, service: WalletService = Depends(wallet_service)
) -> schemas.ConfirmedOperationOut:
    """Answer a request. **This is where the money moves.**

    No request body, and the absence is the design rather than an economy. The
    operation was decided when the request was recorded: its kind, its amount, its
    destination, its pot and its wallet are all on that row, and the confirm call
    names nothing but which row. There is therefore no spelling of this request
    that could carry out a *different* movement from the one somebody asked for -
    not a different amount, not a different account, not a payout where a
    withdrawal was requested. See ``WalletService.confirm``.

    **What this proves is that the caller holds the session, and nothing more.**
    Whoever can open the confirmation can answer it - there is no re-authentication
    here, no password, no second factor. That is deliberate and it is worth stating
    in the route rather than leaving to be discovered: this is an **accident
    guard**, a second look before something irreversible, not a security control.
    What it defends against is a mistyped amount and a habit, not an attacker. A
    stolen session token confirms as easily as it requests, and the answer to that
    is session lifetime (2c) and rate limiting, not a prompt.

    Three refusals, and they are graded by the error module like every other
    domain refusal:

    - ``ConfirmationNotFoundError`` -> **404**, raised identically for a request
      belonging to another account and for an id that names nothing. There is no
      branch here that could tell them apart, which is the same answer
      ``GET /wallets/{id}`` gives.
    - ``ConfirmationAlreadyUsedError`` -> **409**. The request exists, is the
      caller's, and has already been answered - once. It is not a retry-able
      failure and the body says so: a second confirm would move the money twice.
    - ``ConfirmationExpiredError`` -> **409**. Past its window. The request is
      still readable through the ``GET`` below, which is how a client finds out
      *when* it lapsed rather than only that it did.

    And a fourth that is not about the request at all: the answer can be refused
    by the wallet itself - ``WalletFrozenError``, ``InsufficientFundsError`` -
    exactly as the direct call always refused it. **That refusal spends the
    request**, for the three operations that write a ledger row: the attempt and
    its FAILED row are one committed fact, and a request left answerable would let
    a retry be handed the old FAILED row back and report success. The client asks
    again. ``close`` is the exception, because a refused close records nothing at
    all and the whole unit rolls back - see ``_close_wallet``.
    """
    return translate.confirmed_operation_out(
        service.confirm(confirmation_id, datetime.now()), datetime.now()
    )


@router.get(
    "/confirmations/{confirmation_id}", response_model=schemas.ConfirmationOut
)
def get_confirmation(
    confirmation_id: UUID, service: WalletService = Depends(wallet_service)
) -> schemas.ConfirmationOut:
    """One recorded request, and whether it can still be answered.

    A pure read: it commits nothing, and looking at a request does not spend it.
    That is not a courtesy, it is the reason ``EXPIRED`` is derived rather than
    stored - if being asked about a request wrote ``EXPIRED`` over it, this
    endpoint would be a write, and a client that polled it would be changing what
    it was reading.

    ``status`` is what the *server* means by the request's window right now, not
    what is stored. An ``awaiting`` row past its ``expires_at`` is reported as
    ``expired``, because that is the answer the next confirm would act on and a
    response that disagreed with it would be a response worth ignoring. A client
    should read this field rather than compare ``expires_at`` against its own
    clock; see ``translate.confirmation_out``.

    Scoped, like every other read here: another account's request is the same 404
    a random UUID gets.
    """
    return translate.confirmation_out(
        service.get_confirmation(confirmation_id, datetime.now()), datetime.now()
    )
