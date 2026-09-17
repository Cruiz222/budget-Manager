"""Verifying a Google id_token against Google's published keys.

The adapter for ``GoogleIdentityVerifier``, and the second thing in this codebase
that opens a socket to somebody else's server - ``PaystackPaymentProvider`` is the
first. Read with that module, because the shape it establishes is the shape here:
**the third-party library and every exception it can raise stop at this file**, so
no caller above has to know which package is doing the verifying or catch a
``PyJWTError`` to learn that a token was bad.

**Two failures, told apart by what they are about**, which is the whole of this
module's care:

- Something was wrong with **the token** - malformed, signed by a key that is not
  Google's, expired, minted for another application, or missing a claim this
  system requires. That is ``InvalidGoogleTokenError`` and a 401, because the
  caller's move is to obtain a fresh token.
- Something was wrong with **reaching Google** - the key set could not be
  fetched. That is ``GoogleProviderError`` and a 503, because the token was never
  read at all and no change to the request would help. See that class for why it
  is graded with the missing-mail-account errors rather than with the SMS
  provider's 400.

**That distinction is carried entirely by the order of two ``except`` clauses
below, and it would be wrong if they were swapped.** ``PyJWKClientConnectionError``
is a subclass of ``PyJWKClientError``, which is a subclass of ``PyJWTError`` - so a
single ``except PyJWTError`` would report a network failure as a bad token, and an
attacker who can make this server fail to reach Google would be told their *token*
was fine. The narrow clause comes first for that reason, and it is the one place in
this file where the ordering is load-bearing rather than stylistic.
"""

import jwt
from jwt import PyJWKClient

from app.domain.identity.exception import (
    GoogleProviderError,
    InvalidGoogleTokenError,
)
from app.domain.identity.googleIdentity import GoogleIdentity
from app.domain.identity.googleIdentityVerifier import GoogleIdentityVerifier

#: Where Google publishes the public keys it signs id_tokens with.
#:
#: The ``v3`` path, which is the one that serves a JWKS document; the ``v1``,
#: ``v2`` and ``v4`` siblings serve different shapes for different Google APIs and
#: are not interchangeable. The keys rotate - Google publishes new ones before
#: using them and retires old ones after a period during which both verify - which
#: is why this is fetched and cached rather than pinned.
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

#: The issuers Google mints id_tokens under.
#:
#: **Both spellings, because Google uses both** and which one arrives depends on
#: the flow rather than on anything this system controls. Accepting only the
#: ``https://`` form is the common mistake and it fails for exactly the tokens
#: minted by the browser flow this system is built around.
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

#: The algorithms this system will accept, and it is a list of one on purpose.
#:
#: **Pinned rather than inferred from the token's own header**, which is the single
#: most important line in this file. A verifier that read ``alg`` out of the token
#: would accept a token that named ``none`` - the header literally says "no
#: signature" - and would be verifying an attacker's unsigned claims against
#: nothing at all. Pinning it here means an ``alg: none`` token is refused as an
#: algorithm mismatch before any signature is attempted.
#:
#: RS256 is what Google signs with: RSASSA-PKCS1-v1_5 over SHA-256, asymmetric, so
#: the key that verifies is publishable and the key that signs is not. It is also
#: why this integration needs a dependency where the Paystack webhook did not - see
#: ``requirements.txt``.
ALGORITHMS = ["RS256"]

#: How much clock skew to tolerate between this server and Google.
#:
#: Sixty seconds, which is generous for NTP-synchronised machines and small
#: relative to a token lifetime of an hour. It exists because the alternative
#: failure is one-sided and confusing: a server whose clock runs slightly fast
#: refuses tokens that are still perfectly valid, and the refusal says "the token
#: is expired", which sends whoever is debugging it to look at the client.
LEEWAY_SECONDS = 60

#: The claims every id_token this system accepts must carry.
#:
#: **Declared rather than read with ``[]``**, which turns a missing claim into a
#: ``MissingRequiredClaimError`` - and therefore into the same 401 as every other
#: bad token - instead of a ``KeyError`` that would surface as a 500 from inside
#: the adapter. That matters for the two that are easy to omit: ``email`` and
#: ``email_verified`` are only present when the client asked for the ``email``
#: scope, so a client that requested ``openid`` alone gets a token that verifies
#: and asserts nothing this system can use. The refusal is the right answer, and
#: it tells that client to ask for the scope.
#:
#: ``exp`` and ``iat`` are on the list although PyJWT validates the first of them
#: regardless: requiring them says out loud that a token with no expiry is not
#: acceptable, which is a decision rather than a default.
REQUIRED_CLAIMS = ["exp", "iat", "sub", "email", "email_verified"]


class PyJwtGoogleIdentityVerifier(GoogleIdentityVerifier):
    """Check a Google id_token with PyJWT, and report what it asserted.

    **``jwk_client`` is injectable, and that is a testing seam rather than
    configurability.** In production it is ``None`` and the adapter builds the
    client that fetches Google's keys over HTTPS. The tests cannot use that one -
    they would be asserting against whatever Google published that afternoon, and
    they would need the network - so they pass a client whose key set is a
    keypair generated in the test. The seam is here rather than in a
    ``clock``-style parameter because fetching keys is the *only* part of this
    adapter that is not deterministic.

    **One instance is shared across requests and that is safe**, which is worth
    stating because it is not obvious. ``PyJWKClient`` caches the key set and
    refreshes it when it sees a key id it does not hold; concurrent requests
    touching that cache can at worst fetch the same document twice, and the result
    of a fetch is a value either way. Nothing here is mutated per call, so the
    adapter is as shareable as ``Argon2PasswordHasher`` is.
    """

    def __init__(self, settings, jwk_client=None):
        self._client_id = settings.client_id
        self._jwk_client = (
            PyJWKClient(GOOGLE_JWKS_URI) if jwk_client is None else jwk_client
        )

    def verify(self, id_token: str) -> GoogleIdentity:
        """Verify this token's signature and claims, or raise.

        The four checks that make a token mean anything all happen inside
        ``jwt.decode`` below, and it is worth naming them because a reader cannot
        see them from the call:

        1. **The signature**, against the key Google published for the ``kid`` in
           the token's header. This is what makes the claims Google's rather than
           anybody's.
        2. **The algorithm**, against the pinned list above - which is what refuses
           an unsigned token.
        3. **The audience**, against *this installation's* client id. **Without
           this check every Google token on the internet would be a credential
           here**: a token minted for any other application that integrates Google
           is a perfectly valid Google token, and only the audience distinguishes
           it. This is the classic OIDC confusion bug and it is the reason
           ``GoogleSettings`` requires a client id rather than defaulting one.
        4. **The issuer and the window** - that Google minted it, and that it is
           inside its ``exp``/``iat`` bounds within the leeway above.

        Then the claims are turned into an identity, and the one transformation
        worth reading is ``email_verified``.
        """
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=ALGORITHMS,
                audience=self._client_id,
                issuer=list(GOOGLE_ISSUERS),
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWKClientConnectionError as error:
            # Narrow first, and it must stay first. This is the one failure here
            # that is about *this installation* rather than about the token - the
            # keys could not be fetched, so nothing was ever verified. Every other
            # ``PyJWTError`` below, this one included by inheritance, would report
            # it as the caller's mistake.
            raise GoogleProviderError(
                f"the Google key set could not be fetched from {GOOGLE_JWKS_URI}: "
                f"{error}"
            ) from error
        except jwt.PyJWTError as error:
            # Everything else: a malformed token, a signature that does not check
            # out, an algorithm this system does not accept, an audience or issuer
            # that is not ours, a window that has closed, a claim that is missing.
            # One refusal for all of them, because the remedy is the same in every
            # case and because telling them apart describes a forgery to whoever
            # is forging it. See ``InvalidGoogleTokenError``.
            raise InvalidGoogleTokenError(
                f"that is not a valid Google identity token: {error}"
            ) from error

        return GoogleIdentity(
            subject=claims["sub"],
            email=claims["email"],
            email_verified=_verified_flag(claims["email_verified"]),
        )


def _verified_flag(claim) -> bool:
    """Read Google's ``email_verified`` claim as a boolean, or refuse it.

    **Google has shipped this claim as both a boolean and a string**, and the
    string form is the trap rather than a curiosity: ``bool("false")`` is ``True``
    in Python, so an adapter that handed the claim straight to ``GoogleIdentity``
    - or that tested it with ``if claims["email_verified"]`` - would read "Google
    has not proved this address" as "Google has". The consequence is not
    cosmetic: an account holding an address Google has not proved is one whose
    password-reset mail goes to whoever controls that address, which is a working
    takeover path. So the normalisation is the point of this function and the
    strictness is deliberate.

    **Only the two spellings Google emits are accepted**, and anything else is
    refused rather than coerced. A ``1``, a ``"yes"`` or a ``None`` means this
    adapter is looking at a claim it does not understand, and the safe answer is
    to refuse every sign-in until somebody looks - not to pick an interpretation.
    Refusing is the direction that fails loudly; guessing is the direction that
    fails with an account somebody else controls.

    ``GoogleIdentity`` carries a second, stricter check for the same field, and
    the two are not redundant: that one is the domain's guard against *any*
    adapter handing it a non-boolean, and this one is where the provider's actual
    encodings are understood. A future adapter for a different provider would need
    its own version of this function and would inherit that check unchanged.
    """
    if isinstance(claim, bool):
        return claim

    if claim == "true":
        return True

    if claim == "false":
        return False

    raise InvalidGoogleTokenError(
        f"that is not a valid Google identity token: email_verified is "
        f"{claim!r}, which is neither a boolean nor one of the two strings "
        f"Google emits"
    )
