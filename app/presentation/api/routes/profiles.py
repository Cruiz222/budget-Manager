"""``/users/me/profile`` - who the account holder is, and what they may move.

**Two routes, and neither takes an id.** That is the whole of how this file is
safe, and it is worth stating at the top because every other router here has at
least one path with a ``{...}`` in it. ``/users/me`` is the only prefix in this
API that *is* an identity rather than naming one - the actor is resolved from the
``Authorization`` header, and the path segment is the literal word ``me``. So
there is no ``/users/{user_id}/profile`` route for a caller to aim at, and no
code path in this file that could be reached with somebody else's identifier.

That makes the usual 404-for-a-foreign-resource rule inapplicable rather than
absent. ``wallets.py`` has to collapse "not yours" and "does not exist" into one
answer because a wallet id can be substituted; here the substitution cannot be
written down, so the rule is satisfied by the shape of the interface. See
``ProfileService``.

**These are the last two routes in the API that write and move no money**, and
the pairing is the design: a profile is a prerequisite for a tier, a tier is what
decides an amount, and a client that had to make two calls to learn both would be
a client that shows a form without knowing the ceiling it is filling it in under.
"""

from datetime import datetime

from fastapi import APIRouter, Depends

from app.application.profile_service import ProfileService
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import profile_service

router = APIRouter(tags=["identity"])


@router.get("/users/me/profile", response_model=schemas.ProfileOut)
def get_profile(
    service: ProfileService = Depends(profile_service),
) -> schemas.ProfileOut:
    """This account's details, its tier, and the ceilings that tier imposes.

    **200 with an empty profile rather than 404**, and the choice is worth
    defending because 404 is what a "get the thing" endpoint usually does with an
    absent thing. Here the absent thing is the *normal* state: every account that
    existed before this feature has no profile, and so does every account whose
    holder has not filled the form in. Answering 404 would make the majority
    state look like an error, would be indistinguishable to a client from the
    route not existing, and would leave a form unable to tell the difference
    between "nothing given yet" and "wrong URL".

    What comes back instead is an empty form's worth of nulls, the unverified
    tier, and the ceilings that account is currently under - which is more
    information than a 404 and enough to render the screen. See
    ``ProfileOut.profile``.

    **The ceilings are sent for every currency the system knows**, not for the
    wallets this account holds, and ``ProfileOut`` argues that at length. The
    short version is that the limits table is the same for everybody at a tier
    and there is no endpoint that lists a person's wallets to scope by.

    No ``as_of`` parameter and no clock read, unlike ``confirmations.py`` above.
    Nothing in this response is derived from a moment: the tier follows from
    which fields are present, and the ceilings follow from the tier. A request
    made twice returns the same bytes, which is what a client caching a form
    wants and what a limit display has to be for a person to trust it.
    """
    return translate.profile_out(service.standing())


@router.put("/users/me/profile", response_model=schemas.ProfileOut)
def save_profile(
    body: schemas.ProfileIn,
    service: ProfileService = Depends(profile_service),
) -> schemas.ProfileOut:
    """Store this account's profile, creating it if this is the first time.

    **``PUT`` and not ``POST``, because the body is the whole profile.** The
    request carries every field, the resource is the account's single profile
    row, and sending the same body twice leaves the same state - which is what
    ``PUT`` means and what this does. A ``POST`` would suggest a collection that
    can grow, and there is no second profile to create. See ``Profile.revise``
    for why the fields are all present rather than a patch: an absent key and a
    ``null`` cannot both mean "leave this alone" *and* "clear this", so one of
    them has to mean clearing, and the body carrying everything is the only
    version where neither is ambiguous.

    **200 and not 201, even on the first write.** A 201 is right for a request
    that creates something at a new location, and this one does create a row -
    but the resource's location never changes (it is always this account's
    profile), and a client cannot tell a first save from a fifth by anything in
    the response either way. Making the status depend on whether the row happened
    to exist would be reporting the store's internal state rather than the
    caller's situation.

    **A refusal is a 400 in the domain's words.** A country that is not a
    two-letter code, a display name that is blank or over ``MAX_TEXT_LENGTH``,
    and a phone number of the wrong type all arrive as their own named errors
    from ``Profile.__post_init__``, carrying the field they are about - so a
    client can attach the message to the input rather than showing it above the
    form. ``errors`` does the mapping; nothing here catches anything.

    **The moment is read here and nowhere below**, matching every other route
    that writes: ``datetime.now()`` at this line is the one clock reading for the
    request, and it becomes both timestamps on a first save. Passing it in is why
    ``ProfileService.save`` can be asked what it would do at a particular moment,
    which is what lets the "a profile cannot be updated before it was created"
    rule be tested at its boundary.
    """
    return translate.profile_out(
        service.save(
            **translate.profile_fields_in(body),
            now=datetime.now(),
        )
    )
