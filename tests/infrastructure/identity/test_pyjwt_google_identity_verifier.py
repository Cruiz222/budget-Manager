"""The Google adapter, tested against real signed tokens and a real keypair.

**This is where the Paystack lesson is paid forward.** A deposit route once sent
Paystack a reference containing a character Paystack refuses; 2,042 passing tests
could not see it, because every copy of the rule involved had been derived from
the others and the fake accepted whatever it was handed. The lesson was not "write
more tests" but "make the double disagree with the code if the code is wrong" -
which for a signature check means the double must be *real cryptography* rather
than a script.

So nothing in this file is a stub. A 2048-bit RSA keypair is generated per module,
its public half is published as a JWKS document, and tokens are signed with the
private half by ``python-jose``-free means - PyJWT itself, which is what the
adapter uses to verify. That last part is worth being honest about: a bug inside
PyJWT's own RSASSA implementation would not be caught here, and catching it is not
this file's job. What *is* this file's job is every decision the adapter makes
*around* that implementation, and those are exactly the things a hand-written fake
would have agreed with by construction:

    the pinned algorithm      ``alg: none`` must be refused
    the audience              a token Google signed for another app must be refused
    the issuer                both spellings accepted, anything else refused
    the window                expired refused, inside the leeway accepted
    ``email_verified``        the string spellings normalised, anything else refused
    the two error kinds       a bad token is a 401, an unreachable Google is a 503

The last of those is the one a fake could never have caught, and the ordering of
two ``except`` clauses in the adapter is the whole of it - see
``TestSomethingWrongWithReachingGoogle``.

``TEST_OTHER_GOOGLE_CLIENT_ID`` exists for the second line above, and it is the
most valuable constant in this file: without it, a suite that minted every token
for its own client id would be blind to the single check that stops every other
Google-integrated application from being a way into this one.
"""

import base64
import json
from calendar import timegm
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm

from app.domain.identity.exception import (
    GoogleProviderError,
    InvalidGoogleIdentityEmailError,
    InvalidGoogleTokenError,
)
from app.infrastructure.identity.pyjwt_google_identity_verifier import (
    ALGORITHMS,
    LEEWAY_SECONDS,
    PyJwtGoogleIdentityVerifier,
)
from tests.conftest import TEST_GOOGLE_SETTINGS, TEST_OTHER_GOOGLE_CLIENT_ID

KEY_ID = "test-key-1"
SUBJECT = "114988223156872419036"
GOOGLE_EMAIL = "chinedu@example.com"

#: Both spellings Google mints under, because the adapter accepts both.
ISSUER_WITH_SCHEME = "https://accounts.google.com"
ISSUER_BARE = "accounts.google.com"


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


#: Generated once for the module rather than per test. Key generation is the
#: slowest thing here by a wide margin - far slower than anything it is used to
#: prove - and none of these tests mutates a key.
SIGNING_KEY = _keypair()

#: A second key whose *private* half is never published. It exists to sign tokens
#: that the far end cannot check: the kid in their header points at the key above,
#: so the lookup succeeds and the signature is what fails. That is the difference
#: between testing the signature check and testing the key lookup, and only the
#: first is what "the token is not Google's" means.
IMPOSTOR_KEY = _keypair()


def _jwks() -> dict:
    """The public half of the signing key, as Google publishes keys."""
    jwk = json.loads(RSAAlgorithm.to_jwk(SIGNING_KEY.public_key()))
    jwk.update({"kid": KEY_ID, "use": "sig", "alg": "RS256", "kty": "RSA"})
    return {"keys": [jwk]}


class RecordingJwkClient(PyJWKClient):
    """A ``PyJWKClient`` whose key set is a fixture instead of an HTTPS response.

    **Subclasses the real client rather than replacing it**, which is the point:
    everything above ``fetch_data`` - parsing the JWKS document, matching a
    token's ``kid`` to a key, caching - is the library's real code, so those are
    still exercised. The only thing replaced is the socket. A hand-written
    resolver returning ``SIGNING_KEY.public_key()`` would have skipped the ``kid``
    matching entirely, and the unknown-``kid`` refusal below would have had
    nothing to test against.

    ``fail`` is how a fetch that never arrives is expressed: the real client
    raises ``PyJWKClientConnectionError`` from inside ``fetch_data`` when the
    request fails, so raising it from here is the same failure at the same seam.
    """

    def __init__(self, document=None, fail=None):
        super().__init__("https://www.googleapis.com/oauth2/v3/certs")
        self._document = _jwks() if document is None else document
        self._fail = fail
        self.fetches = 0

    def fetch_data(self):
        self.fetches += 1
        if self._fail is not None:
            raise self._fail
        return self._document


def build(**overrides) -> PyJwtGoogleIdentityVerifier:
    jwk_client = overrides.pop("jwk_client", None)
    if jwk_client is None:
        jwk_client = RecordingJwkClient(**overrides)
    return PyJwtGoogleIdentityVerifier(
        settings=TEST_GOOGLE_SETTINGS, jwk_client=jwk_client
    )


def claims(**overrides) -> dict:
    """A complete, valid set of id_token claims, so a test can spoil exactly one.

    Anchored on the real clock, because the adapter checks the window against
    ``datetime.now`` inside PyJWT and there is no ``now`` parameter to pass. The
    window is an hour wide with a minute of leeway on either side, so the few
    milliseconds this file takes to run cannot matter.
    """
    moment = datetime.now(timezone.utc)
    values = {
        "iss": ISSUER_WITH_SCHEME,
        "aud": TEST_GOOGLE_SETTINGS.client_id,
        "sub": SUBJECT,
        "email": GOOGLE_EMAIL,
        "email_verified": True,
        "iat": moment - timedelta(minutes=1),
        "exp": moment + timedelta(hours=1),
    }
    values.update(overrides)
    return values


def sign(payload=None, key=None, kid=KEY_ID) -> str:
    return jwt.encode(
        claims() if payload is None else payload,
        SIGNING_KEY if key is None else key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def unsigned(payload=None) -> str:
    """An ``alg: none`` token, built by hand.

    Deliberately not ``jwt.encode(..., algorithm="none")``, and the difference
    matters for what this proves. Asking a library to produce an unsigned token
    tests that the library will - and a future version of it refusing would turn
    this test green for the wrong reason. A header, a payload and an empty
    signature is what an attacker writes, and it depends on nothing.

    **The claims are converted the way ``jwt.encode`` converts them**, which is
    the one thing this hand-built token has to get right to be testing what it
    says it is. ``jwt.encode`` rewrites ``exp``, ``iat`` and ``nbf`` from
    ``datetime`` to integer epoch seconds before signing, so a real token's
    payload holds numbers; ``json.dumps`` raises ``TypeError`` on a ``datetime``
    instead. Left unconverted, this test failed on the payload rather than on the
    signature and would have gone on passing if the algorithm check were removed -
    which is the failure it exists to catch.
    """

    def segment(value) -> str:
        raw = json.dumps(
            value, separators=(",", ":"), default=_epoch_seconds
        ).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = {"alg": "none", "typ": "JWT", "kid": KEY_ID}
    return f"{segment(header)}.{segment(claims() if payload is None else payload)}."


def _epoch_seconds(value) -> int:
    """A ``datetime`` as the integer ``jwt.encode`` would have written it.

    ``timegm`` over ``utctimetuple`` and not ``int(value.timestamp())``, because
    those differ for a value with sub-second precision and the point is to match
    the library rather than to be approximately right.
    """
    if isinstance(value, datetime):
        return timegm(value.utctimetuple())

    raise TypeError(f"{value!r} is not JSON serializable")


class TestATokenThatChecksOut:
    def test_it_reports_what_google_asserted(self):
        identity = build().verify(sign())

        assert identity.subject == SUBJECT
        assert identity.email == GOOGLE_EMAIL
        assert identity.email_verified is True

    def test_the_address_keeps_googles_spelling(self):
        """The adapter does not fold, and neither does ``GoogleIdentity``.

        Folding is ``User``'s rule, applied where an address is *stored*, and the
        duplicate lookup calls ``find_by_email`` which folds. A fold here would be
        a second implementation of "the same address written two ways is one
        account" - the thing ``fold_email``'s docstring exists to prevent.
        """
        identity = build().verify(sign(claims(email="Chinedu@Example.COM")))

        assert identity.email == "Chinedu@Example.COM"

    @pytest.mark.parametrize("issuer", [ISSUER_WITH_SCHEME, ISSUER_BARE])
    def test_both_issuer_spellings_are_accepted(self, issuer):
        """Google mints under both, and which one arrives depends on the flow.

        Accepting only the ``https://`` form is the common mistake, and it fails
        for exactly the tokens the browser flow produces.
        """
        assert build().verify(sign(claims(iss=issuer))).subject == SUBJECT

    def test_an_unverified_address_is_reported_rather_than_refused(self):
        """The adapter reports the fact; the use case decides what it means.

        This is the split that keeps the takeover argument readable next to the
        reset flow it protects, and it is asserted here because it would be easy
        to "tighten" this adapter by refusing an unverified address - which would
        move a policy into the one layer that cannot see why it exists.
        """
        identity = build().verify(sign(claims(email_verified=False)))

        assert identity.email_verified is False


class TestTheSignature:
    def test_a_token_signed_by_somebody_else_is_refused(self):
        """The published key's ``kid``, the impostor's private key.

        The lookup succeeds, so the failure is the signature and not the key
        search - which is what makes this a test of RSASSA rather than of the
        ``kid`` matching tested below.
        """
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(key=IMPOSTOR_KEY))

    def test_an_unsigned_token_is_refused(self):
        """``alg: none``, the single most common JWT verification failure there is.

        The defence is ``ALGORITHMS`` in the adapter - a list of one, pinned
        rather than read from the token's own header. A verifier that consulted the
        header would accept this token, because the header is the attacker's and it
        says there is nothing to check.
        """
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(unsigned())

    def test_the_pinned_algorithm_list_is_rs256_alone(self):
        """The defence named directly, so widening it is a visible decision.

        An empty list, a list containing ``none``, or one that grew a symmetric
        algorithm would each be a real vulnerability and none of them would fail
        any other test in this file.
        """
        assert ALGORITHMS == ["RS256"]

    def test_a_token_with_an_unknown_kid_is_a_bad_token_and_not_a_broken_install(self):
        """A ``kid`` that is not in the key set, and the distinction that matters.

        ``PyJWKClient`` raises for this, and that exception is a subclass of
        ``PyJWTError`` - so it is reported as a bad token. It would be *wrong* to
        report it as a provider failure: Google is perfectly reachable, and telling
        an attacker the difference between "I could not reach Google" and "your
        token is not one of ours" is the kind of feedback this refusal exists to
        avoid.
        """
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(kid="a-key-google-never-published"))

    def test_something_that_is_not_a_token_at_all_is_a_bad_token(self):
        """**This is the test that pins the ``except`` ordering in the adapter.**

        A garbage string makes ``PyJWKClient`` fail while parsing the header, well
        before any network call. The narrow ``except`` for connection failures
        comes first, and if those two clauses were ever swapped - or collapsed
        into one ``except PyJWTError`` - this input would be reported as a 503 and
        every caller would be told the installation was broken because they sent
        nonsense.
        """
        with pytest.raises(InvalidGoogleTokenError):
            build().verify("not-a-jwt-at-all")

    @pytest.mark.parametrize("token", ["", "a.b.c", "a.b", "....."])
    def test_malformed_tokens_are_refused_without_reaching_the_network(self, token):
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(token)


class TestTheAudience:
    """The check that stops every other Google application from being a way in.

    A token Google signed for *somebody else's* application is a real, valid,
    correctly-signed Google token. Only the ``aud`` claim says who it was minted
    for, so a verifier that skipped this comparison would accept a token that
    anybody who has ever integrated "Sign in with Google" could obtain. This is
    the classic OIDC confusion bug, and it is the reason ``GoogleSettings``
    requires a client id rather than defaulting to an empty one: a comparison
    against nothing is not a comparison.
    """

    def test_a_token_minted_for_another_application_is_refused(self):
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(claims(aud=TEST_OTHER_GOOGLE_CLIENT_ID)))

    def test_a_token_with_no_audience_is_refused(self):
        payload = claims()
        del payload["aud"]

        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(payload))

    def test_our_own_client_id_keeps_working(self):
        """Stated so the two above cannot pass by refusing everything."""
        assert build().verify(sign(claims(aud=TEST_GOOGLE_SETTINGS.client_id))) is not None


class TestTheIssuer:
    @pytest.mark.parametrize(
        "issuer",
        ["evil.example.com", "", "https://accounts.google.com.evil.test"],
    )
    def test_anything_that_is_not_google_is_refused(self, issuer):
        """Including the lookalike domain, which is the one worth naming.

        ``https://accounts.google.com.evil.test`` is a host somebody controls,
        whose name *begins with* Google's. Any check written as a prefix or a
        substring rather than an equality would accept it.
        """
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(claims(iss=issuer)))


class TestTheWindow:
    def test_an_expired_token_is_refused(self):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        expired = claims(iat=past, exp=past + timedelta(minutes=5))

        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(expired))

    def test_a_token_inside_the_leeway_is_accepted(self):
        """Expired by seconds, and deliberately still good.

        The leeway exists because the failure without it is one-sided and
        confusing: a server whose clock runs slightly fast refuses tokens that are
        still perfectly valid, and the refusal says "expired", which sends whoever
        is debugging it to look at the client. The margin below is half the
        allowance, so this cannot pass by accident on a machine whose clock
        happens to agree.
        """
        moment = datetime.now(timezone.utc)
        just_expired = claims(
            iat=moment - timedelta(minutes=10),
            exp=moment - timedelta(seconds=LEEWAY_SECONDS // 2),
        )

        assert build().verify(sign(just_expired)).subject == SUBJECT

    def test_a_token_that_expires_far_too_soon_is_refused(self):
        """Beyond the leeway, so the margin above is bounded rather than infinite."""
        moment = datetime.now(timezone.utc)
        long_expired = claims(
            iat=moment - timedelta(hours=2),
            exp=moment - timedelta(seconds=LEEWAY_SECONDS * 10),
        )

        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(long_expired))

    def test_a_token_with_no_expiry_is_refused(self):
        """``exp`` is in the adapter's required list, so its absence is a refusal.

        A token that never expires is one that works forever once stolen, and
        PyJWT would only complain about its absence if asked to. Requiring it is a
        decision rather than a default.
        """
        payload = claims()
        del payload["exp"]

        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(payload))


class TestWhetherTheAddressIsProved:
    """Where Google's two encodings of one claim are turned into a boolean.

    This is the adapter's most consequential normalisation. ``bool("false")`` is
    ``True`` in Python, so an adapter that handed the claim straight through - or
    that tested it with ``if claims["email_verified"]`` - would read "Google has
    not proved this address" as "Google has". The address then becomes the target
    of a password-reset mail, and whoever controls it takes the account. See
    ``UnverifiedGoogleEmailError`` for the chain in full.
    """

    def test_a_real_boolean_is_passed_through(self):
        assert build().verify(sign(claims(email_verified=True))).email_verified is True
        assert build().verify(sign(claims(email_verified=False))).email_verified is False

    def test_the_string_true_is_read_as_true(self):
        """The spelling Google has historically shipped."""
        assert build().verify(sign(claims(email_verified="true"))).email_verified is True

    def test_the_string_false_is_read_as_false(self):
        """**The trap.** ``bool("false")`` is ``True`` - see the class docstring."""
        assert bool("false") is True  # the trap, still true

        assert build().verify(sign(claims(email_verified="false"))).email_verified is False

    @pytest.mark.parametrize("value", ["TRUE", "False", "yes", "1", 1, 0, None])
    def test_anything_else_is_refused_rather_than_guessed(self, value):
        """Not coerced - refused, and the direction is deliberate.

        A ``1`` or a ``"yes"`` means this adapter is looking at a claim it does
        not understand. Guessing would be the direction that fails with an account
        somebody else controls; refusing is the direction that fails loudly, on
        every sign-in, until somebody looks. That asymmetry is the whole of why
        this raises rather than picking an interpretation.
        """
        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(claims(email_verified=value)))

    def test_an_absent_claim_is_refused(self):
        """``email_verified`` is only present when the client asked for the scope.

        A client that requested ``openid`` alone gets a token that verifies and
        asserts nothing this system can use. Refusing tells that client to ask for
        the scope, which is the actionable answer - and it is a 401 rather than a
        ``KeyError`` 500 because the claim is in the adapter's required list.
        """
        payload = claims()
        del payload["email_verified"]

        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(payload))


class TestTheOtherRequiredClaims:
    @pytest.mark.parametrize("claim", ["sub", "email", "iat"])
    def test_a_missing_required_claim_is_a_bad_token_and_not_a_crash(self, claim):
        """**The difference between a 401 and a 500, and why it is declared.**

        Read with ``[]`` instead of declaring the claims, each of these would be a
        ``KeyError`` raised from inside the adapter - which is a 500, on a request
        an attacker fully controls. The required list turns all three into the same
        refusal as any other bad token.
        """
        payload = claims()
        del payload[claim]

        with pytest.raises(InvalidGoogleTokenError):
            build().verify(sign(payload))

    def test_a_blank_email_is_not_a_missing_one(self):
        """It verifies, and ``GoogleIdentity`` is what refuses it.

        The adapter checks that a claim is *present*; whether its value is usable
        is the value object's guard. So ``""`` gets past ``jwt.decode`` and is
        refused one line later, which is the division the domain's docstring
        states - the adapter reports, the domain validates.

        **The class is named rather than merely being "not that other one".** Both
        guards are unreachable from a client, and the sentence they produce is the
        thing an operator reads: ``InvalidGoogleTokenError`` would say the *caller*
        sent a bad token, when what happened is that this adapter read the wrong
        claim out of one. Asserting the negative alone - which is what this test
        did until the class existed to be named - would also pass on a ``KeyError``
        or a ``TypeError``, which is the direction a refactor breaks in.
        """
        with pytest.raises(InvalidGoogleIdentityEmailError):
            build().verify(sign(claims(email="")))


class TestSomethingWrongWithReachingGoogle:
    """The one failure here that is about the installation rather than the token.

    A 503 against a 401, and the difference is not cosmetic: a caller told their
    token was bad will go and get a new one, and it will fail the same way, because
    the token was never read.
    """

    def test_a_key_set_that_cannot_be_fetched_is_a_provider_failure(self):
        from jwt import PyJWKClientConnectionError

        failing = RecordingJwkClient(
            fail=PyJWKClientConnectionError("connection refused")
        )

        with pytest.raises(GoogleProviderError):
            build(jwk_client=failing).verify(sign())

    def test_it_is_not_reported_as_a_bad_token(self):
        """**The subclass relationship that makes the ordering load-bearing.**

        ``PyJWKClientConnectionError`` is a subclass of ``PyJWKClientError``, which
        is a subclass of ``PyJWTError`` - so a single broad ``except`` would swallow
        this and report a network failure as the caller's mistake. Asserted rather
        than described, because the assertion is what fails on the day a library
        version changes the hierarchy and the narrow clause quietly stops being the
        distinguishing one.
        """
        from jwt import PyJWKClientConnectionError, PyJWKClientError, PyJWTError

        assert issubclass(PyJWKClientConnectionError, PyJWKClientError)
        assert issubclass(PyJWKClientError, PyJWTError)

    def test_the_failure_names_where_the_keys_should_have_come_from(self):
        """An operator reading this needs the URL, not the word "Google"."""
        from jwt import PyJWKClientConnectionError

        failing = RecordingJwkClient(fail=PyJWKClientConnectionError("nope"))

        with pytest.raises(GoogleProviderError) as raised:
            build(jwk_client=failing).verify(sign())

        assert "googleapis.com" in str(raised.value)

    def test_a_key_set_that_parses_but_does_not_match_is_a_bad_token(self):
        """An empty key set is reachable, and it is *not* a provider failure.

        The distinction worth holding: the document arrived, so Google was
        reachable. There is simply no key in it for this token, which is a fact
        about the token.
        """
        empty = RecordingJwkClient(document={"keys": []})

        with pytest.raises(InvalidGoogleTokenError):
            build(jwk_client=empty).verify(sign())
