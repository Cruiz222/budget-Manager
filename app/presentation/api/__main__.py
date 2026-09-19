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

**This paragraph now describes one of two ways to run this, and the other one is
``compose.yaml``.** That file is the deployment: it passes ``--host 0.0.0.0``
because the only thing that can reach the port is Caddy on the compose network,
and Caddy is what terminates TLS. So the bind above is the right answer for
running this on the machine you are sitting at - which is what ``python3 -m
app.presentation.api`` is for - and no longer a statement about how the software
is deployed. The default did not change; what changed is that there is now
somewhere else for a deployment to be, and the deployment makes its own choice
in the place that choice belongs.

The reason has now been rewritten twice and the conclusion has not changed
either time. It used to say the header named a user and proved nothing, so
anyone on the network could act as anyone through the server. That header is
gone and that hole is closed. Phase 2a rewrote it to point at real credentials
crossing plain HTTP, which is still true here and still why loopback is the
default. Phase 4 added the deployment, which answers that objection with a proxy
rather than with a bind address. Rewritten rather than left both times, because
a rationale that outlives its reason reads as a constraint that has lifted -
and after the second rewrite it is worth saying plainly that this one *has*
lifted, for everything except a bare local run.
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
