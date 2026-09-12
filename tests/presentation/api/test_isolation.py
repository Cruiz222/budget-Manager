"""A stranger's resources are not there.

**This is the phase's point, and the HTTP-level counterpart of
``TestTheSchedulerIsNotAPrivilegedActor``.** Phase 1a removed every way to read a
wallet without naming its owner, and made a foreign wallet raise exactly what a
nonexistent one raises. What it could not do was prove the *wire* kept that
property: a presentation layer is where a distinction gets reintroduced, because
that is the layer whose whole job is turning one thing into another. A handler
that decided to be helpful - "that wallet belongs to someone else" - would undo
the phase with one sentence.

So every test in this class asks the same question twice: once about a resource
that exists and belongs to somebody else, and once about an id that does not
exist at all. The assertion is that the two answers are *identical* - status and
body - which is the property, and it is stronger than checking for a 404. A 404
with a different body is a 404 that tells an attacker the wallet is real.
"""

import pytest

from tests.presentation.api.conftest import ALICE, BOB


@pytest.fixture
def alice_world(client, as_user, open_wallet, open_pot, create_plan):
    """Alice with a wallet, a pot on it, and a plan drawing on it."""
    alice = as_user(ALICE)
    wallet_id = open_wallet(alice)
    open_pot(wallet_id, alice)
    plan = create_plan(wallet_id, alice)
    assert plan.status_code == 201, plan.text
    return {
        "wallet": wallet_id,
        "plan": plan.json()["plan_id"],
    }


class TestTheOwnerCanReachTheirOwnThings:
    """The control, without which every assertion below passes for the wrong reason.

    If the world fixture quietly failed to build anything, every 404 in this
    module would still be a 404 - and the suite would be green and meaningless.
    So these run first, in the same class, against the same fixture.
    """

    def test_the_wallet_is_there(self, client, as_user, alice_world):
        response = client.get(
            f"/wallets/{alice_world['wallet']}", headers=as_user(ALICE)
        )

        assert response.status_code == 200

    def test_the_plan_is_there(self, client, as_user, alice_world):
        response = client.get(
            f"/plans/{alice_world['plan']}", headers=as_user(ALICE)
        )

        assert response.status_code == 200


class TestAStrangersWalletIsNotThere:
    def test_reading_it_is_a_404(self, client, as_user, alice_world):
        response = client.get(
            f"/wallets/{alice_world['wallet']}", headers=as_user(BOB)
        )

        assert response.status_code == 404

    def test_the_answer_is_the_one_a_typo_gets(self, client, as_user, alice_world, unknown_id):
        """The assertion the whole phase rests on: the two are indistinguishable.

        Not "both are 404" - *identical*. Status, headers-class and body. A body
        that differed would be enough to enumerate which wallet ids are real, and
        the domain went to some trouble to make sure there is no branch between
        the foreign case and the missing one; this is what says the API has not
        added one back.
        """
        theirs = client.get(
            f"/wallets/{alice_world['wallet']}", headers=as_user(BOB)
        )
        nobody = client.get(f"/wallets/{unknown_id}", headers=as_user(BOB))

        assert theirs.status_code == nobody.status_code
        assert theirs.json() == nobody.json()

    def test_its_ledger_is_a_404(self, client, as_user, alice_world):
        response = client.get(
            f"/wallets/{alice_world['wallet']}/transactions", headers=as_user(BOB)
        )

        assert response.status_code == 404

    def test_its_pots_are_a_404(self, client, as_user, alice_world):
        response = client.get(
            f"/wallets/{alice_world['wallet']}/funds", headers=as_user(BOB)
        )

        assert response.status_code == 404

    def test_its_plans_are_a_404(self, client, as_user, alice_world):
        response = client.get(
            f"/wallets/{alice_world['wallet']}/plans", headers=as_user(BOB)
        )

        assert response.status_code == 404


class TestAStrangerCannotWriteIntoItEither:
    """Reads and writes are refused by the same mechanism, and that is the design.

    There is no authorisation check in this layer to get wrong. A write fails
    because the service cannot *load* the wallet it was told to write to, so the
    refusal is the same not-found a read gets - not a second rule that happens to
    agree with the first today.
    """

    def test_opening_a_pot_on_it_is_a_404(self, client, as_user, alice_world):
        response = client.post(
            f"/wallets/{alice_world['wallet']}/funds",
            json={"name": "Sneaky", "kind": "personal"},
            headers=as_user(BOB),
        )

        assert response.status_code == 404

    def test_creating_a_plan_on_it_is_a_404(self, client, as_user, create_plan, alice_world):
        response = create_plan(alice_world["wallet"], as_user(BOB))

        assert response.status_code == 404

    def test_the_pot_was_not_created(self, client, as_user, alice_world):
        """The write is refused, not merely reported as refused.

        Worth its own test because the two are different failures with the same
        status code. A handler that caught the error and returned 404 *after* the
        unit of work committed would pass every assertion above and leave a
        stranger's pot on somebody else's wallet.
        """
        client.post(
            f"/wallets/{alice_world['wallet']}/funds",
            json={"name": "Sneaky", "kind": "personal"},
            headers=as_user(BOB),
        )

        pots = client.get(
            f"/wallets/{alice_world['wallet']}/funds", headers=as_user(ALICE)
        ).json()
        assert [pot["name"] for pot in pots] == ["Vacation"]


class TestAStrangersPlanIsNotThere:
    def test_reading_it_is_a_404(self, client, as_user, alice_world):
        response = client.get(
            f"/plans/{alice_world['plan']}", headers=as_user(BOB)
        )

        assert response.status_code == 404

    def test_its_history_is_a_404(self, client, as_user, alice_world):
        response = client.get(
            f"/plans/{alice_world['plan']}/runs", headers=as_user(BOB)
        )

        assert response.status_code == 404

    @pytest.mark.parametrize("verb", ["pause", "resume", "cancel"])
    def test_steering_it_is_a_404(self, client, as_user, alice_world, verb):
        """Steering is a write, and it is refused the same way a read is.

        The plan is loaded by a scoped read before anything is changed, so a
        stranger's plan is a plan that does not exist - and ``pause`` on a plan
        that does not exist never reaches the aggregate that would have refused
        it for a different reason.
        """
        response = client.post(
            f"/plans/{alice_world['plan']}/{verb}", headers=as_user(BOB)
        )

        assert response.status_code == 404

    def test_editing_it_is_a_404(self, client, as_user, alice_world):
        response = client.put(
            f"/plans/{alice_world['plan']}/instructions",
            json={"instructions": []},
            headers=as_user(BOB),
        )

        assert response.status_code == 404

    def test_it_was_not_cancelled(self, client, as_user, alice_world):
        """The steering refusal, checked at the source rather than at the status.

        A 404 is only half the claim; the other half is that the plan is still
        active. This is the assertion that would catch a handler which reported
        not-found after the transition had already been committed.
        """
        client.post(f"/plans/{alice_world['plan']}/cancel", headers=as_user(BOB))

        plan = client.get(
            f"/plans/{alice_world['plan']}", headers=as_user(ALICE)
        ).json()
        assert plan["status"] == "active"


def test_two_users_are_two_accounts(client, as_user, open_wallet):
    """The isolation above is not an artefact of everything 404-ing.

    Both users can open a wallet and read it back, and they are different
    wallets. Without this, a bug that made ``open_wallet`` fail silently would
    turn the whole module green.
    """
    alice_wallet = open_wallet(as_user(ALICE))
    bob_wallet = open_wallet(as_user(BOB))

    assert alice_wallet != bob_wallet
    assert client.get(f"/wallets/{alice_wallet}", headers=as_user(ALICE)).status_code == 200
    assert client.get(f"/wallets/{bob_wallet}", headers=as_user(BOB)).status_code == 200
