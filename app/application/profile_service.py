"""Reading and writing the identity fields an account holder has given us.

The KYC-shaped half of the application layer, and the *smallest* service here:
two operations, one aggregate, no money. It is a service rather than a use case
per file because both operations are a load or a load-and-store against one
repository, and splitting them would be two files that each say "open a unit,
reach for ``profiles``, close the unit".

**Not merged into ``WalletService``, and the pressure to merge it is worth
naming** because it is the opposite of the pressure on ``PlanService``. Plans and
wallets are separate services because a plan read should not carry the thing that
moves money around with it. Profiles and wallets are separate for the mirror
reason: a caller that wants to read somebody's legal name should not have to hold
a handle on the object that can spend their balance. ``WalletService`` is
constructed with an actor and so is this, but they are built separately by the
composition root and a route asks for the one it needs.

**The tier is derived on every read and stored nowhere**, which is
``tier_for``'s decision and this class's obligation. There is no ``set_tier``
here and no method that could become one - completing a profile is the only way
to move a tier, and it moves as a consequence rather than as a request.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.profile import Profile
from app.domain.identity.tier import Tier, tier_for


@dataclass(frozen=True)
class ProfileStanding:
    """A profile - possibly none - and the tier derived from it.

    **Two fields rather than one, because a caller almost never wants only the
    profile.** The question a route answers with this is "what are we allowed to
    do", and the answer needs the tier; the question it answers on the write path
    is "what did that change", and the answer is the *new* tier. Deriving it in
    the route instead would work and would mean two places could disagree about
    the one derivation - the shape of error this feature exists to prevent, since
    a wrongly derived tier is not a wrong label, it is a wrong limit.

    ``profile`` is ``None`` for an account that has never given one, and that is
    the ordinary case rather than an error - see ``tier_for``. ``tier`` is never
    ``None``: every account has a tier, and the absence of a profile is one of
    the two ways to be in the lower one.

    **``user_id`` is carried even though the service already knows it**, and the
    reason is the ``None`` case rather than the ordinary one. When there is a
    profile, a caller can read the owner off it; when there is not, there is no
    object left to ask, and a presentation that wanted to say whose empty profile
    this is would have to reach past this value for the actor. Carrying it makes
    the answer complete on its own - the same reason ``EmailChangeOutcome``
    carries its ``User`` rather than leaving a route to resolve one.
    """

    user_id: uuid.UUID
    profile: Profile | None
    tier: Tier

    @property
    def is_complete(self) -> bool:
        """Whether the profile says enough to be at the higher tier.

        Worth having as its own name even though it is ``tier``'s other spelling,
        because the two are read by different callers: a client rendering a form
        asks *is this finished*, and a client rendering a limit asks *what am I*.
        Reading ``tier is Tier.IDENTIFIED`` for the first would work today and
        would quietly mean something else the day a third tier arrives.
        """
        return self.tier is Tier.IDENTIFIED


class ProfileService:
    """The account holder's own profile, and the tier it puts them at.

    **The actor is bound at construction and there is no method that takes a
    user id**, which is ``PlanService``'s arrangement and matters more here.
    Every method on a plan service is scoped to a wallet, so a mistake is a
    wallet somebody else owns; every method here *is* a person's identity, so
    the only thing between a caller and a stranger's legal name would be the
    argument they chose to pass. Removing the argument removes the mistake
    rather than guarding it - the same move ``WalletRepository`` made when it
    deleted ``get_by_id``.

    Concretely: the route that reads a profile takes no id from the path, no id
    from the body, and no id from a query string. The only ``user_id`` this
    class will ever use came from ``current_actor``, which resolved it from a
    session token.

    **There is deliberately no way to read somebody else's profile, and therefore
    no "404 for a foreign profile" rule to write.** Decision 66's rule exists
    because ``WalletService`` takes a ``wallet_id`` and must collapse "not yours"
    and "does not exist" into one answer. Here there is no id to substitute: a
    caller asking for "my profile" cannot name somebody else's, so the two cases
    do not arise. The rule is satisfied by the shape of the interface rather than
    by a branch in it, which is the stronger version.

    This is also why there is no ``get_owned``-style pair on
    ``ProfileRepository`` and why its read returns ``None``: with one caller and
    one row, "absent" and "not permitted" are the same impossibility.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, *, actor: uuid.UUID):
        self._unit_of_work_factory = unit_of_work_factory
        # Who this service acts for, for every method it has. Required and
        # keyword-only, for the reason ``WalletService`` and ``PlanService`` give:
        # a default of ``None`` would be a service that reads nothing and looks
        # broken rather than one that refuses.
        self._actor = actor

    def standing(self) -> ProfileStanding:
        """This account's profile and tier, read in one unit.

        One unit for the single read, which is not about atomicity - there is one
        statement here. It is that every read in this codebase goes through a
        unit, and a repository reached outside one is a repository holding a
        connection nobody closed.

        **The absence of a profile is a normal answer**, and this is the method
        that says so: it returns ``ProfileStanding(profile=None,
        tier=Tier.UNVERIFIED)`` for an account that has never filled the form in.
        Nothing raises, and the route answers 200 with an empty profile and the
        unverified limits. That is the shape a client wants - a form to fill,
        with the ceiling it is currently under already visible - and it is also
        what makes "every existing account is tier 0 on rollout" true without a
        backfill: there is nothing to migrate, because absence already answers.
        """
        uow = self._unit_of_work_factory.start()
        try:
            profile = uow.profiles.find_for_user(self._actor)
            return ProfileStanding(
                user_id=self._actor, profile=profile, tier=tier_for(profile)
            )
        finally:
            # Read-only, so the rollback releases the connection and there is
            # nothing to commit - ``ResolveActorFromSession``'s arrangement.
            uow.rollback()

    def save(
        self,
        display_name: str,
        legal_first_name: str | None,
        legal_last_name: str | None,
        date_of_birth: date | None,
        phone: str | None,
        country: str | None,
        address_line: str | None,
        now: datetime,
    ) -> ProfileStanding:
        """Store this account's profile, creating it if it does not exist yet.

        **Create and edit are one method, and the branch is on the row's
        existence rather than on a flag.** A separate ``create_profile`` and
        ``edit_profile`` would be two entry points that write the same seven
        fields to the same row, and a caller holding a form has no way to know
        which it is - the person filling their profile in for the first time and
        the person fixing a typo press the same button. Making the *existence of
        the row* the only input that differs is what lets the route be one
        ``PUT``, and ``PUT`` is the honest verb: the body is the whole profile,
        not a patch of it.

        The two paths differ in exactly one respect, and it is the one worth
        being careful about: **``created_at`` is set once and never moved.** On
        create it is ``now``; on edit it is carried across from the loaded row by
        ``Profile.revise``, because "when did you first give us this" is a fact
        about the row that no amount of editing changes. Writing ``now`` into it
        on every save would make the column mean "last updated" - which is what
        ``updated_at`` already means, so the two would become one field spelled
        twice.

        **The load and the store are one unit**, so the row cannot be created by
        this call and edited by a concurrent one against a stale read. That is
        why the profile is loaded here rather than passed in, even though the
        route could have read it a moment earlier through ``standing`` - a
        decision made from a value read outside the unit is a decision made from
        a value that may already be wrong.

        The field-by-field refusal - a country that is not a code, a birth date
        that is a ``datetime`` - is the aggregate's and happens inside
        ``Profile(...)`` and ``revise``. Nothing is validated here, deliberately:
        this method has no rule of its own, and a check written here would be a
        second opinion about a question ``Profile.__post_init__`` has already
        answered. A refusal propagates out of the ``try`` with nothing committed,
        which leaves the database exactly as it was.
        """
        uow = self._unit_of_work_factory.start()
        try:
            existing = uow.profiles.find_for_user(self._actor)

            if existing is None:
                profile = Profile(
                    user_id=self._actor,
                    display_name=display_name,
                    legal_first_name=legal_first_name,
                    legal_last_name=legal_last_name,
                    date_of_birth=date_of_birth,
                    phone=phone,
                    country=country,
                    address_line=address_line,
                    created_at=now,
                    updated_at=now,
                )
            else:
                # ``revise`` mutates in place and returns None, so the object
                # that gets saved below is the one that was loaded - which is
                # what keeps this a *single* write of a row that already exists
                # rather than a delete-and-recreate.
                existing.revise(
                    display_name=display_name,
                    legal_first_name=legal_first_name,
                    legal_last_name=legal_last_name,
                    date_of_birth=date_of_birth,
                    phone=phone,
                    country=country,
                    address_line=address_line,
                    now=now,
                )
                profile = existing

            # The store is reached only after the aggregate has accepted the
            # values, because the constructor above aborts the call before a
            # statement is issued. The ``CHECK (updated_at >= created_at)`` in
            # the schema is the same rule written a second time, and it is not
            # redundant: it is what catches a row written by something that did
            # not come through this method.
            uow.profiles.save(profile)
            uow.commit()
        finally:
            uow.rollback()

        # Built after the commit, from the profile that was actually stored, so
        # the tier returned is the tier of the row that is now durable - not of
        # the values the caller asked for. They are the same object, and stating
        # the order is what keeps them so.
        return ProfileStanding(
            user_id=self._actor, profile=profile, tier=tier_for(profile)
        )
