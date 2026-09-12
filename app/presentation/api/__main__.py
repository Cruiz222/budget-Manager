"""Run the API: ``python3 -m app.presentation.api``.

Equivalent to::

    .venv/bin/uvicorn app.presentation.api.app:create_app --factory

Both forms are correct; this one exists because the CLI has a ``python -m`` entry
point and the API should not be the thing that needs a second tool remembered.
The ``--factory`` form is what a deployment wants, since it can then add
``--host``, ``--port``, ``--workers`` and ``--reload`` without this file having to
predict which of them somebody will need.

**It binds to loopback.** A process listening on every interface would take
credentials from anyone who can reach it, and this API speaks plain HTTP - so a
password would cross the network in the clear on the way in, and a session token
would cross it on every request after that. ``127.0.0.1`` is the address that
keeps both to the machine the server is running on, and binding wider is a
decision a deployment makes explicitly - by passing its own ``--host``, which is
what the ``--factory`` form above exists to allow - rather than one this default
makes for it.

The reason was rewritten in Phase 2a and the conclusion was not. This paragraph
used to say the header named a user and proved nothing, so anyone on the network
could act as anyone through the server. The header is gone and that hole is
closed, but plain HTTP is not TLS and the credentials are real now, so the bind
is still right for a different reason. Rewritten rather than left, because a
rationale that outlives its reason reads as a constraint that has lifted.
"""

import uvicorn

from app.presentation.api.app import create_app

#: Where an unconfigured run listens. See the module docstring for why loopback.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    uvicorn.run(create_app(), host=DEFAULT_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
