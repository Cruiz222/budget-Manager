"""Where this layer's pages live, and the one address that leaves the building.

**The prefix is ``/app`` and it is not decoration.** The API's namespace is flat
and prefix-free by deliberate policy - ``/wallets/{id}``, ``/sessions``,
``/confirmations/{id}`` - so every one of this layer's pages has to sit somewhere
the API has not already claimed. ``/wallets/{id}`` is an API path; ``/app/wallets/
{id}`` is this file's. A test asserts the two sets never meet, because the day
they do the loser is whichever router was registered second, silently.

**``/app`` and not ``/ui``, ``/browser`` or ``/web``.** It is the shortest of
them and the only one that reads as a *noun* a person would recognise - the
address bar saying ``/app/wallets/...`` is a sentence about their money, and
``/ui/wallets/...`` is a sentence about the software. It also happens to be what
the API's own title says this is: an application, with two ways in.

**Everything here is a constant rather than a computed path**, including the ones
built from ids. A URL is composed at the one place that knows the id - a route
returning a redirect, a template linking to a wallet - and a builder function
here would be a third place that knew the shape of a URL, free to disagree with
the two that matter.
"""

#: Where this layer's pages begin. Everything below is spelled with it.
PREFIX = "/app"

#: The static mount's path. Under the prefix, so a stylesheet request cannot be
#: confused with an API path by anything reading a log.
STATIC_PREFIX = f"{PREFIX}/static"

#: The page a person lands on, and the one every successful sign-in redirects to.
#:
#: It is ``/app/`` with a trailing slash rather than ``/app``, and the difference
#: is the one Starlette makes: the router is mounted at ``/app`` with a root route
#: at ``/``, so ``/app/`` is the page and ``/app`` is a redirect to it. Returning
#: the trailing form directly keeps a sign-in from costing a second hop.
LANDING_PATH = f"{PREFIX}/"

#: Where a session is proved. Three paths, because there are three ways in: a
#: password, and a Google identity that is either a sign-in or a registration.
SIGN_IN_PATH = f"{PREFIX}/sign-in"
SIGN_UP_PATH = f"{PREFIX}/sign-up"
SIGN_OUT_PATH = f"{PREFIX}/sign-out"

#: The path segment a payer is returned to after paying.
#:
#: **It is a path and not a URL, and it is the only string in this module that
#: leaves the building.** See ``callback_url`` - the join that makes it absolute
#: happens here and nowhere else.
RETURN_PATH = LANDING_PATH


def callback_url(public_base_url: str | None) -> str | None:
    """The absolute address Paystack sends a payer back to, or ``None``.

    **The one place the installation's public origin meets this layer's routes**,
    and it is a function here rather than a line in ``create_app`` for a reason
    worth stating: the path is *this* module's fact and the origin is
    ``settings``'s, and the composition root is the only frame holding both. Put
    the join in ``settings`` and a rename of ``RETURN_PATH`` breaks a module that
    has never heard of a route; put it in ``create_app`` and the API's factory
    learns this layer's URL vocabulary. A one-line function on the side that owns
    the path keeps the knowledge where it belongs.

    ``None`` in, ``None`` out - an installation that has not been told its public
    origin takes deposits with no return address, which is a supported state and
    not an error. See ``public_base_url_from_environment``.

    **The origin arrives already stripped of a trailing slash**, so the join is a
    concatenation rather than a ``urljoin``. That is deliberate: ``urljoin``
    would silently *replace* the path for a base like ``https://example.com/x``,
    and the failure would be a payer landing on a page this system does not
    serve.
    """
    if public_base_url is None:
        return None
    return f"{public_base_url}{RETURN_PATH}"
