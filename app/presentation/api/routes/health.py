from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.presentation.api import schemas

router = APIRouter(tags=["health"])


@router.get("/health", response_model=schemas.HealthOut)
def health(request: Request):
    """Whether this process can reach its database.

    **The one endpoint that reaches no actor and reads no identity**, and its
    absence is a statement rather than an oversight - the same kind
    ``build_notifier`` makes in the composition root. A probe that needed a token
    would be a probe that could not run before anybody had registered, which is
    precisely when somebody wants to ask whether the service is up. So there is
    no ``Depends(current_actor)`` here, no service is built, and this is the one
    path through the API that opens a unit of work without first establishing who
    is asking why.

    That makes it the endpoint to check when authentication is broken and you
    want to know whether *anything* is working: an answer here with a 401
    everywhere else means the process and its database are fine and the problem
    is in a token.

    What it does: opens a unit of work and discards it. That is a real check
    rather than a liveness flag - ``SqliteUnitOfWorkFactory.start`` opens a
    connection, applies the schema and begins a transaction, so it fails if the
    file is missing, unwritable, locked or corrupt. What it deliberately does not
    do is read anything: no counts, no versions, no rows. See ``HealthOut``.

    A failure is a 503 rather than a 500, because the two say different things to
    whoever is watching. A 500 says "this request broke"; a 503 says "this
    service is not in a state to serve requests", which is what a probe is
    reporting and what a load balancer needs in order to stop sending traffic.
    """
    factory = request.app.state.unit_of_work_factory
    try:
        uow = factory.start()
        uow.rollback()
    except Exception:
        # Deliberately broad, and one of the very few places in this codebase
        # where that is right. A readiness probe exists to answer "can this
        # process serve?" for *any* reason it cannot - a missing file, a locked
        # database, a full disk, a bug in the schema. Catching narrowly would
        # mean an unanticipated failure escapes as a 500, and a 500 is the one
        # answer a probe must never give, because it reads as "the check itself
        # is broken" rather than "the service is down".
        return JSONResponse(
            status_code=503, content={"status": "unavailable"}
        )
    return schemas.HealthOut(status="ok")
