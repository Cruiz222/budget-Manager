"""What a browser sends, and what is refused before it is read.

Three jobs, and they are all about the same thing: a form post arrives from a
place a header never did, so this module is where the differences are handled
rather than spread across twelve handlers.

**Reading a field.** Every route declares its fields as optional strings and
reads them through the four functions below, rather than declaring them as
required with FastAPI's ``Form(...)``. That is a deliberate trade and it costs a
class - see ``MalformedFormError`` for the other half of it. FastAPI answers a
missing required form field with a 422 whose body is a validation report keyed by
field name; correct, machine-readable, and a page a person cannot act on. What
this layer wants for an empty box is a sentence, so the fields are read here and
an empty one is refused in the one error contract the rest of the system uses.

**The check that runs before anything else.** A cookie is attached by the
browser rather than by the code, so any page anywhere can make a browser issue
a request that arrives wearing this session. Three layers stand between that and
a withdrawal, and ``reject_cross_site`` is the third - the one that could
plausibly be got wrong, because it has to compare two values that are not
spelled the same way.

**Nothing here reads a form.** The parse is FastAPI's, done while the handler's
parameters are solved, and it is why ``python-multipart`` is a dependency. What
this module does is decide what a parsed field *means*.
"""

from urllib.parse import urlsplit

from fastapi import Request

from app.presentation.web.errors import CrossSitePostError, MalformedFormError


def required(value: str | None, name: str) -> str:
    """A field a form cannot do without, or a refusal naming it.

    The ``name`` is the field as the *form* spells it - ``amount``, ``currency``,
    ``password`` - and it is used verbatim in the sentence, because the person
    reading it has just been looking at the box with that label on it. Naming the
    parameter instead (``payload_amount``, ``body_currency``) would be naming
    something they never saw.

    **Whitespace is not a value.** A form field is a text box, and a text box
    that has been clicked in and left arrives as ``" "`` rather than as nothing -
    so a blank check that did not strip would accept a password of three spaces
    and send it to be hashed. Every reader in this module strips for that reason,
    and this is the one where it matters: a whitespace password is a password the
    person will not be able to reproduce.
    """
    text = (value or "").strip()
    if not text:
        raise MalformedFormError(f"{name} is required")
    return text


def optional(value: str | None) -> str | None:
    """A field that may be absent or blank, as ``None`` when it is either.

    **``None`` and ``""`` are collapsed on purpose**, and the collapse is the
    whole content of the function. Every use of this is a value whose absence is
    meaningful and whose blankness means the same thing - a deposit's reference,
    which the system generates when nobody supplied one - so a caller that had to
    distinguish ``""`` from ``None`` would be distinguishing two spellings of one
    fact. The domain already does exactly this, in ``_text``, for the same reason
    one layer out.
    """
    text = (value or "").strip()
    return text or None


def looks_like_an_address(identifier: str) -> bool:
    """Whether a sign-in form's single box was filled in with an address.

    **This is the one place in this system that sniffs an identifier**, and it is
    worth being explicit about, because ``routes/sessions.py`` refuses to do it
    three lines of code away in the other presentation. That route's argument is
    that the API's body *declares* which of the two it carries, so a single
    lookup that guessed would be accepting two spellings of one thing - and it is
    a good argument, which is why the JSON contract still has two fields.

    A form has one box. A person signing in on a phone types their number and a
    person signing in on a laptop types their address, and neither is going to
    select from a dropdown saying which kind of string they just typed. So the
    question is not whether to sniff but where, and the answer is at the edge,
    in a module whose whole job is reading what a browser sends - which keeps the
    branch in ``routes.py`` an explicit ``if`` that names both use cases rather
    than a guess buried in a lookup.

    ``"@"`` is the test because it is total for this system's two kinds: an
    address is the only one that contains one, and ``fold_phone`` strips a number
    down to digits and a leading ``+``. It is deliberately not a regex matching
    an email address, because that would make "is this an address?" a question
    with three answers - yes, no, and *nearly* - and the third one would have
    nowhere to go. A string that contains an ``@`` is tried as an address and
    fails as one; the refusal names no cause, which is what sign-in refusals do.
    """
    return "@" in identifier


def reject_cross_site(request: Request) -> None:
    """Refuse a POST that a page of ours did not send.

    **It compares hosts and not origins, and that is the whole of what makes it
    work behind a proxy.** This application runs behind Caddy, which terminates
    TLS: the request that arrives here carries ``Host: budget.example`` while the
    browser's ``Origin`` reads ``https://budget.example``. A comparison of the
    two strings would refuse *every* real user, and it would refuse them on the
    one deployment that matters. So the schemes are stripped and the net locations
    are compared - which is also why the port has to survive the comparison: a
    ``Host`` of ``example.com:8443`` and an ``Origin`` of ``https://example.com``
    are different origins and must not be equal here.

    **``X-Forwarded-Proto`` is deliberately not consulted.** It is the header
    that would let a scheme comparison be made, and it is untrustworthy in
    exactly the direction that matters: uvicorn's ``--forwarded-allow-ips``
    defaults to loopback, so a value that arrived from anywhere else is ignored -
    and a check that read a header it could not distinguish "Caddy set this" from
    "an attacker set this" about would be a check an attacker can satisfy. Hosts
    are the same on both sides without any proxy cooperation, so the comparison
    needs no header to be trustworthy.

    **An absent header is allowed, and that is the correct default rather than a
    hole.** ``SameSite=Lax`` - the first layer - means a cross-site POST does not
    carry the cookie at all, so a forger's request arrives with no session and is
    refused by ``web_actor`` before this is reached. What this function defends
    is the narrower case the cookie cannot: a browser that ignores ``SameSite``,
    or one where the cookie was sent anyway. Those requests carry an ``Origin``,
    because every browser that sends ``Origin`` on a POST sends it always. So
    "no header" means a client that is not a browser sending a cross-site form -
    which is a client that could have set an ``Origin`` if it wanted to, and whose
    actual defence is the credential it does or does not hold.

    **A mismatching ``Origin`` is refused without consulting ``Referer``.** Both
    headers are on the browser's forbidden list, so a page cannot set either; a
    request carrying an ``Origin`` that is not ours was therefore either made by a
    browser on a page that is not ours, or constructed deliberately. Falling back
    to ``Referer`` in that case would only ever rescue the second kind.
    """
    host = request.headers.get("host")
    if not host:
        raise CrossSitePostError("this request carried no Host header")

    origin = request.headers.get("origin")
    if origin is None:
        origin = request.headers.get("referer")
    if origin is None:
        return

    if _host_of(origin) != host:
        raise CrossSitePostError(
            "this request did not come from a page of this application"
        )


def _host_of(origin: str) -> str:
    """The host and port of a URL, or the empty string if it has neither.

    An unparseable value and a value with no host both come back as ``""``, which
    never equals a real ``Host`` and is therefore always refused. That is the
    safe direction and it is the reason this is a function with a name rather
    than a ``urlsplit`` call at the comparison: the interesting input is the one
    that is not a URL - ``Origin: null``, which a sandboxed frame and a ``file://``
    page send - and ``urlsplit("null").netloc`` is ``""``, which is exactly the
    answer that refuses it.
    """
    try:
        return urlsplit(origin).netloc
    except ValueError:
        return ""
