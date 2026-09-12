"""Creating plans and steering them, over HTTP.

The other half of the phase's surface, and the half with a claim attached: a plan
is the only thing this API can create that *will* move money, and it is allowed
here because creating one moves none. Everything below is that claim, played out
- a plan can be described, listed, paused, resumed, cancelled and rewritten, and
its balance is never touched.

The headline case is the fixture's own: a monthly payout from the available
balance to a bank account. The interesting one is at the bottom, where a plan that
releases locked money is created against a pot holding nothing at all.
"""

from tests.presentation.api.conftest import ALICE


class TestCreatingAPlan:
    def test_it_is_created_with_the_shape_it_was_given(self, client, as_user, open_wallet, create_plan):
        """The whole request comes back as the plan, which is the round trip.

        Every field a client sent is present in the answer and unaltered - the
        cadence, the anchor, the amount, the label, the destination. A plan is
        long-lived and read far more often than it is written, so "what did I
        actually set up?" is the first question anybody asks of one, and the
        answer has to be the request.
        """
        headers = as_user()
        response = create_plan(open_wallet(headers), headers)

        assert response.status_code == 201
        plan = response.json()
        assert plan["name"] == "Rent"
        assert plan["source"] == "available"
        assert plan["status"] == "active"
        assert plan["schedule"] == {
            "cadence": "monthly",
            "anchor": "2026-03-02T12:00:00",
        }
        assert plan["instructions"] == [
            {
                "action": "payout",
                "amount": {"amount": "2500.00", "currency": "NGN"},
                "label": "March rent",
                "destination": {
                    "kind": "bank_account",
                    "identifier": "0123456789",
                    "name": "Chinedu Okafor",
                    "details": {"bank_code": "058"},
                },
            }
        ]

    def test_it_is_drawn_on_the_wallet_it_names(self, client, as_user, open_wallet, create_plan):
        headers = as_user()
        wallet_id = open_wallet(headers)

        plan = create_plan(wallet_id, headers).json()

        assert plan["wallet_id"] == wallet_id
        assert [one["plan_id"] for one in client.get(f"/wallets/{wallet_id}/plans", headers=headers).json()] == [
            plan["plan_id"]
        ]

    def test_it_starts_at_its_anchor_with_nothing_done(self, client, as_user, open_wallet, create_plan):
        """``next_due_at`` is derived from the anchor and never accumulated.

        The anchor here is in the past - 2 March, months before this test runs -
        and the answer is still the anchor itself. That is the property the
        schedule was designed around: a plan fires on the day its owner chose, not
        on today-plus-a-month, so a plan created late is not quietly shifted
        forward to suit the clock it happened to be created on.

        It also makes this assertion deterministic, which an assertion about "the
        next occurrence from now" could never be.
        """
        headers = as_user()

        plan = create_plan(open_wallet(headers), headers).json()

        assert plan["completed_runs"] == 0
        assert plan["next_due_at"] == "2026-03-02T12:00:00"
        assert plan["ends_on"] is None

    def test_what_one_run_costs_is_the_sum_of_its_lines(
        self, client, as_user, open_wallet, create_plan, payout_line
    ):
        """``total_to_move`` is the plan's own answer, sent rather than left to be summed.

        Two lines, so the answer is not just the first one echoed back - which is
        the failure a single-instruction test could not catch. A client that
        recomputed this would be a second implementation of "what does this cost",
        and this is the number it is checked against when a run fires.
        """
        headers = as_user()
        response = create_plan(
            open_wallet(headers),
            headers,
            instructions=[
                payout_line("1000.00", "Rent"),
                payout_line("500.50", "Agent"),
            ],
        )

        assert response.status_code == 201
        assert response.json()["total_to_move"] == {"amount": "1500.50", "currency": "NGN"}

    def test_a_term_is_resolved_to_a_date_by_the_domain(self, client, as_user, open_wallet, create_plan):
        """"12 months from the anchor" becomes a day, and the day is the domain's arithmetic.

        The request says ``term``; the response says ``ends_on``. That asymmetry is
        the design: the term is how a person says it and the date is what gets
        stored, because a date stays true and "12 months" has to be resolved
        against something forever. The resolving is ``Duration.end_from``, which is
        the same call the CLI makes - so a plan created here and one created at a
        terminal end on the same day.
        """
        headers = as_user()
        response = create_plan(
            open_wallet(headers), headers, term={"amount": 12, "unit": "months"}
        )

        assert response.status_code == 201
        assert response.json()["ends_on"] == "2027-03-02"

    def test_an_end_date_can_be_given_outright(self, client, as_user, open_wallet, create_plan):
        headers = as_user()

        response = create_plan(open_wallet(headers), headers, ends_on="2027-06-30")

        assert response.status_code == 201
        assert response.json()["ends_on"] == "2027-06-30"

    def test_a_monthly_plan_is_not_irreversible(self, client, as_user, open_wallet, create_plan):
        """The flag a client uses to decide whether to offer a cancel button.

        Sent so the button is not offered and then refused - which is the shape of
        bad interface that a 409-only API produces.
        """
        headers = as_user()

        response = create_plan(open_wallet(headers), headers)

        assert response.json()["is_irreversible"] is False


class TestAPlanThatReleasesLockedMoney:
    """Creating one is allowed here, and this class is where that is made visible.

    It looks like it should not be: nothing can put money into a pot in this
    phase, so a plan drawn on a pot is a plan drawn on nothing. The rule that
    makes it legal is the domain's own - ``PlanService.create_plan`` deliberately
    does not check whether the pot holds enough or whether it has matured, because
    a savings plan's whole purpose is to *become* affordable. That check happens
    when a run fires, and the run records a blocked reason.

    So this is not a hole in the phase boundary. The boundary is about balances,
    and creating this plan moves none. What it does is let a client build the
    shape of a commitment it cannot yet fund - which is exactly what a savings
    product is.
    """

    def test_it_can_be_created_against_an_empty_pot(self, client, as_user, open_wallet, open_pot, create_plan):
        headers = as_user()
        wallet_id = open_wallet(headers)
        open_pot(wallet_id, headers, name="Vacation")

        response = create_plan(
            wallet_id,
            headers,
            source="locked",
            fund_name="Vacation",
            ends_on="2027-03-02",
            instructions=[
                {"action": "release", "amount": "1000.00", "label": "To savings"}
            ],
        )

        assert response.status_code == 201
        assert response.json()["source"] == "locked"
        assert response.json()["fund_id"] is not None

    def test_it_is_irreversible(self, client, as_user, open_wallet, open_pot, create_plan):
        """Which is the product, not a limitation - see ``SavingsPlan.is_irreversible``.

        The client is told so it can grey the controls out rather than offer an
        action whose only outcome is a 409. The refusal itself is tested in
        ``test_errors``.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        open_pot(wallet_id, headers, name="Vacation")

        plan = create_plan(
            wallet_id,
            headers,
            source="locked",
            fund_name="Vacation",
            ends_on="2027-03-02",
            instructions=[
                {"action": "release", "amount": "1000.00", "label": "To savings"}
            ],
        ).json()

        assert plan["is_irreversible"] is True

    def test_a_pot_that_does_not_exist_is_a_404(self, client, as_user, open_wallet, create_plan):
        """Naming a pot the wallet does not have, checked at creation rather than at the first run.

        The alternative is discovering it weeks later from a blocked run in a
        scheduler nobody is watching, which is the kind of failure this check
        exists to convert into an immediate answer.
        """
        headers = as_user()

        response = create_plan(
            open_wallet(headers),
            headers,
            source="locked",
            fund_name="No Such Pot",
            ends_on="2027-03-02",
            instructions=[
                {"action": "release", "amount": "1000.00", "label": "To savings"}
            ],
        )

        assert response.status_code == 404
        assert response.json()["error"] == "FundNotFoundError"


class TestSteeringAPlan:
    def test_pausing_and_resuming(self, client, as_user, open_wallet, create_plan):
        """Both directions, because a one-way transition would pass a test that only went one way."""
        headers = as_user()
        plan_id = create_plan(open_wallet(headers), headers).json()["plan_id"]

        paused = client.post(f"/plans/{plan_id}/pause", headers=headers)
        resumed = client.post(f"/plans/{plan_id}/resume", headers=headers)

        assert paused.json()["status"] == "paused"
        assert resumed.json()["status"] == "active"

    def test_pausing_does_not_move_the_plan_along(self, client, as_user, open_wallet, create_plan):
        """The reason pausing is safe to offer without a confirmation step.

        A pause that advanced the run counter would silently skip the payment it
        was interrupted on, and the plan would come back to service owing one less
        month than its owner agreed to. So the counter and the due date are
        asserted unchanged, not merely the status.
        """
        headers = as_user()
        plan_id = create_plan(open_wallet(headers), headers).json()["plan_id"]

        paused = client.post(f"/plans/{plan_id}/pause", headers=headers).json()

        assert paused["completed_runs"] == 0
        assert paused["next_due_at"] == "2026-03-02T12:00:00"

    def test_cancelling_ends_it(self, client, as_user, open_wallet, create_plan):
        headers = as_user()
        plan_id = create_plan(open_wallet(headers), headers).json()["plan_id"]

        response = client.post(f"/plans/{plan_id}/cancel", headers=headers)

        assert response.json()["status"] == "cancelled"

    def test_a_fresh_plan_has_no_run_history(self, client, as_user, open_wallet, create_plan):
        """Empty rather than absent. Runs are the scheduler's record and nothing has fired.

        ``[]`` and a 404 are different answers here for the same reason they are
        for a wallet's ledger: one means nothing has happened yet, the other means
        the caller has the wrong id.
        """
        headers = as_user()
        plan_id = create_plan(open_wallet(headers), headers).json()["plan_id"]

        response = client.get(f"/plans/{plan_id}/runs", headers=headers)

        assert response.status_code == 200
        assert response.json() == []


class TestEditingAPlansInstructions:
    def test_the_whole_set_is_replaced(
        self, client, as_user, open_wallet, create_plan, payout_line
    ):
        """Two lines in, one line out - so "replaced" is not "appended".

        The distinction is invisible when the new set is the same size as the old
        one, which is what editing one line into one line would have tested. The
        ``PUT`` body is a whole new set rather than a patch precisely because a
        patch would have to address anonymous lines by position, and a client
        holding a stale list would rewrite the wrong one.
        """
        headers = as_user()
        plan_id = create_plan(
            open_wallet(headers),
            headers,
            instructions=[
                payout_line("2500.00", "March rent"),
                payout_line("500.00", "Agent"),
            ],
        ).json()["plan_id"]

        response = client.put(
            f"/plans/{plan_id}/instructions",
            json={"instructions": [payout_line("3000.00", "April rent")]},
            headers=headers,
        )

        assert response.status_code == 200
        assert len(response.json()["instructions"]) == 1
        assert response.json()["instructions"][0]["label"] == "April rent"
        assert response.json()["total_to_move"] == {"amount": "3000.00", "currency": "NGN"}

    def test_the_currency_comes_from_the_plan_not_from_the_request(
        self, client, as_user, open_wallet, create_plan, payout_line
    ):
        """A bare amount in a different wallet's currency is a different amount.

        The request body has no currency in it anywhere, so there is no way for a
        caller to name one - the plan's own currency is what the numbers are read
        in. Checked by editing a plan in a USD wallet and watching the answer come
        back in USD: the same string ``"3000.00"`` means two different sums, and
        only the plan can say which one this is.
        """
        headers = as_user()
        wallet_id = open_wallet(headers, currency="USD")
        plan_id = create_plan(wallet_id, headers).json()["plan_id"]

        response = client.put(
            f"/plans/{plan_id}/instructions",
            json={"instructions": [payout_line("3000.00", "April rent")]},
            headers=headers,
        )

        assert response.json()["instructions"][0]["amount"] == {
            "amount": "3000.00",
            "currency": "USD",
        }

    def test_a_plan_that_releases_locked_money_refuses_it(self, client, as_user, open_wallet, open_pot, create_plan):
        """The same rule as ``cancel``, and the same 409.

        An edit is a way to change what a commitment does, so allowing one on a
        plan that releases locked money would be cancelling it by another name.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        open_pot(wallet_id, headers, name="Vacation")
        plan_id = create_plan(
            wallet_id,
            headers,
            source="locked",
            fund_name="Vacation",
            ends_on="2027-03-02",
            instructions=[
                {"action": "release", "amount": "1000.00", "label": "To savings"}
            ],
        ).json()["plan_id"]

        response = client.put(
            f"/plans/{plan_id}/instructions",
            json={
                "instructions": [
                    {"action": "release", "amount": "1.00", "label": "A little less"}
                ]
            },
            headers=headers,
        )

        assert response.status_code == 409
        assert response.json()["error"] == "IrreversibleReleasePlanError"


def test_a_plan_created_by_one_user_is_invisible_to_another(client, as_user, open_wallet, create_plan):
    """The isolation property, stated once more where plans are the subject.

    ``test_isolation`` is where this belongs and where it is covered properly;
    this is here because a plan is the one resource in this phase that a user
    *creates* rather than merely owns, and "created it" is a tempting thing to
    mistake for permission.
    """
    alice_plan = create_plan(open_wallet(as_user(ALICE)), as_user(ALICE)).json()

    response = client.get(f"/plans/{alice_plan['plan_id']}", headers=as_user())

    assert response.status_code == 404
