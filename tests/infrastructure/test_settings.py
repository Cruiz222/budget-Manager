from app.infrastructure.settings import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_PORT,
    database_path,
    describe_configuration,
    describe_termii_configuration,
    from_environment,
    paystack_from_environment,
    termii_from_environment,
)


def environment(**overrides):
    """A complete SMTP configuration, with any variable overridden or removed.

    A dict rather than a patched ``os.environ``, because ``from_environment``
    takes its environment as an argument. That is the whole reason it does: the
    one module that reads the real environment is still testable without
    touching it.
    """
    values = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USER": "me@example.com",
        "SMTP_PASSWORD": "hunter2",
        "BUDGET_NOTIFY_TO": "me@example.com",
    }
    values.update(overrides)
    return {name: value for name, value in values.items() if value is not None}


class TestAnUnconfiguredInstall:
    """A fresh install has no mail account, and that is normal rather than broken.

    Returning ``None`` rather than raising is what lets the scheduler run at all
    on an install that has not set email up. The tick still warns, still records
    the warning and still pays - the warnings simply have nowhere to go.
    """

    def test_no_host_means_no_settings(self):
        assert from_environment(environment(SMTP_HOST=None)) is None

    def test_no_recipient_means_no_settings(self):
        assert from_environment(environment(BUDGET_NOTIFY_TO=None)) is None

    def test_an_empty_string_counts_as_absent(self):
        """A variable set to "" is a variable someone meant to fill in."""
        assert from_environment(environment(SMTP_HOST="")) is None

    def test_whitespace_counts_as_absent(self):
        assert from_environment(environment(BUDGET_NOTIFY_TO="   ")) is None

    def test_nothing_configured_at_all_is_still_no_settings(self):
        assert from_environment({}) is None

    def test_a_missing_sender_means_no_settings(self):
        """A mail needs a From, and neither variable that could supply one is set."""
        assert (
            from_environment(environment(SMTP_USER=None, BUDGET_NOTIFY_FROM=None))
            is None
        )


class TestSayingWhatIsMissing:
    """The failure mode of a misconfigured notifier is silence.

    "SMTP_HOST is not set" is the difference between a two-minute fix and an
    afternoon of guessing, which is the entire reason this function exists
    separately from ``from_environment``.
    """

    def test_a_complete_configuration_has_nothing_to_report(self):
        assert describe_configuration(environment()) is None

    def test_a_missing_host_is_named(self):
        assert describe_configuration(environment(SMTP_HOST=None)) == (
            "SMTP_HOST is not set"
        )

    def test_a_missing_recipient_is_named(self):
        assert describe_configuration(environment(BUDGET_NOTIFY_TO=None)) == (
            "BUDGET_NOTIFY_TO is not set"
        )

    def test_a_non_numeric_port_is_named_with_its_value(self):
        """Naming the bad value matters: a typo is invisible otherwise."""
        assert describe_configuration(environment(SMTP_PORT="five-eight-seven")) == (
            "SMTP_PORT is not a number: 'five-eight-seven'"
        )

    def test_a_missing_sender_names_both_alternatives(self):
        assert describe_configuration(
            environment(SMTP_USER=None, BUDGET_NOTIFY_FROM=None)
        ) == "SMTP_USER or BUDGET_NOTIFY_FROM is not set"


class TestWhatACompleteConfigurationLooksLike:
    def test_it_reads_every_variable(self):
        settings = from_environment(environment())

        assert settings.host == "smtp.example.com"
        assert settings.recipient == "me@example.com"
        assert settings.username == "me@example.com"
        assert settings.password == "hunter2"

    def test_the_port_defaults_to_the_starttls_submission_port(self):
        """587 is the one that speaks STARTTLS; 25 is the one that usually does not."""
        assert DEFAULT_PORT == 587
        assert from_environment(environment()).port == 587

    def test_an_explicit_port_is_read_as_a_number(self):
        settings = from_environment(environment(SMTP_PORT="2525"))

        assert settings.port == 2525

    def test_the_sender_falls_back_to_the_username(self):
        """The common case: you send from the account you authenticate as."""
        settings = from_environment(environment(BUDGET_NOTIFY_FROM=None))

        assert settings.sender == "me@example.com"

    def test_an_explicit_sender_wins(self):
        settings = from_environment(environment(BUDGET_NOTIFY_FROM="alerts@x.y"))

        assert settings.sender == "alerts@x.y"

    def test_the_recipient_is_independent_of_the_account(self):
        """Where the warning goes is not where it is sent from."""
        settings = from_environment(environment(BUDGET_NOTIFY_TO="chinedu@x.y"))

        assert settings.recipient == "chinedu@x.y"
        assert settings.sender == "me@example.com"

    def test_credentials_are_optional(self):
        """A relay on the local network needs no login.

        The sender still has to come from somewhere - that is a separate
        requirement - so ``BUDGET_NOTIFY_FROM`` supplies it here and the account
        credentials are absent.
        """
        settings = from_environment(
            environment(
                SMTP_USER=None,
                SMTP_PASSWORD=None,
                BUDGET_NOTIFY_FROM="alerts@example.com",
            )
        )

        assert settings.username is None
        assert settings.password is None
        assert settings.sender == "alerts@example.com"


class TestTheStartTlsFlag:
    """On by default, and switched off explicitly rather than fallen back from.

    Tolerating a server that does not offer STARTTLS would mean accepting a
    downgrade to plaintext on a connection carrying a password, and an attacker
    who can strip the capability from the server's greeting can make that happen.
    A local debugging server has no certificate, so it needs the flag set to 0 -
    which is a deliberate act rather than something the code decides.
    """

    def test_it_is_on_by_default(self):
        assert from_environment(environment()).starttls is True

    def test_it_can_be_switched_off(self):
        assert from_environment(environment(SMTP_STARTTLS="0")).starttls is False

    def test_the_usual_spellings_of_false_all_work(self):
        for spelling in ("0", "false", "FALSE", "no", "off"):
            assert (
                from_environment(environment(SMTP_STARTTLS=spelling)).starttls
                is False
            )

    def test_anything_else_is_on(self):
        for spelling in ("1", "true", "yes", "on"):
            assert (
                from_environment(environment(SMTP_STARTTLS=spelling)).starttls
                is True
            )


class TestWhereTheDatabaseLives:
    """The second thing this module reads, and the reason it was renamed.

    These tests are about a *different* kind of setting from the ones above, and
    the contrast is the point. Mail has an unconfigured state and this does not:
    an install with no mail account still works, and an install with no database
    has nothing to work on. So there is a default here where there is a ``None``
    there.
    """

    def test_it_falls_back_to_the_file_the_cli_has_always_used(self):
        """The absent case is the behaviour that existed before the API did.

        This is what makes the API additive: an installation that sets nothing
        and runs the server gets the same database the CLI would have used, so
        the two presentations are looking at one set of money rather than two.
        """
        assert DEFAULT_DATABASE_PATH == "budget.db"
        assert database_path({}) == "budget.db"

    def test_the_variable_overrides_the_default(self):
        assert database_path({"BUDGET_DB": "/var/lib/budget/prod.db"}) == (
            "/var/lib/budget/prod.db"
        )

    def test_an_empty_value_is_not_a_path(self):
        """``""`` would otherwise be a database called nothing at all.

        The same rule ``_text`` applies to every other setting, and it matters
        more here than there: an operator who exported ``BUDGET_DB=`` while
        clearing a value gets the default and their data, rather than an empty
        filename SQLite would happily create in the working directory.
        """
        assert database_path({"BUDGET_DB": ""}) == "budget.db"

    def test_a_whitespace_value_is_not_a_path_either(self):
        assert database_path({"BUDGET_DB": "   "}) == "budget.db"

    def test_surrounding_whitespace_is_trimmed(self):
        """An exported path can pick up a stray space, and the file is real.

        Trimming rather than refusing, for the same reason ``User`` folds an
        email: the person who typed it has done nothing wrong, and the value is
        unambiguous once trimmed.
        """
        assert database_path({"BUDGET_DB": " /tmp/x.db\n"}) == "/tmp/x.db"

    def test_it_is_not_a_mail_setting_and_does_not_depend_on_one(self):
        """The two halves of this module are independent.

        A complete mail configuration does not imply a database path and the
        reverse, which is what makes it safe for the API to read one without
        reading the other.
        """
        assert database_path(environment()) == "budget.db"
        assert from_environment({"BUDGET_DB": "/tmp/x.db"}) is None


class TestThePaymentKey:
    """The second reader, and the one whose absent case is the dangerous one.

    Mail's ``None`` means "say nothing"; this one's means "accept nothing", and
    the two are the same value doing opposite jobs. That is why the absent cases
    are tested first and in the same shape as the mail ones: a blank value counts
    as unset here, and the reason is sharper than it is there. A key of
    whitespace verifies every signature against whitespace - which is not a crash
    and not a refusal but a working-looking integration that anybody who reads the
    source can forge against.
    """

    def test_a_key_is_read(self):
        settings = paystack_from_environment({"PAYSTACK_SECRET_KEY": "sk_test_abc"})

        assert settings.secret_key == "sk_test_abc"

    def test_no_key_means_no_settings(self):
        assert paystack_from_environment({}) is None

    def test_an_empty_string_counts_as_absent(self):
        """The variable somebody exported while clearing it."""
        assert paystack_from_environment({"PAYSTACK_SECRET_KEY": ""}) is None

    def test_whitespace_counts_as_absent(self):
        assert paystack_from_environment({"PAYSTACK_SECRET_KEY": "   "}) is None

    def test_surrounding_whitespace_is_trimmed(self):
        """A key pasted out of a dashboard can carry a newline, and a key with
        one in it signs nothing - so the value is trimmed rather than kept whole.
        """
        settings = paystack_from_environment(
            {"PAYSTACK_SECRET_KEY": " sk_test_abc\n"}
        )

        assert settings.secret_key == "sk_test_abc"

    def test_it_is_not_a_mail_setting_and_does_not_depend_on_one(self):
        """The independence claim stated in both directions.

        A fully configured mail install implies no payment key, and a payment key
        implies no mail settings - which is what makes it correct for the API to
        read this one on every webhook without reading the other, and for a
        receipt to be attempted on an install that takes money and sends nothing.
        """
        assert paystack_from_environment(environment()) is None
        assert from_environment({"PAYSTACK_SECRET_KEY": "sk_test_abc"}) is None


class TestTheSmsProvider:
    """The third reader, and the one whose absent case has no fallback at all.

    Mail's ``None`` means "say nothing", the payment one's means "accept nothing",
    and this one's means "this flow cannot run". That is the difference worth
    testing here rather than the field names: an install with no Termii account
    cannot send a signup code and has no second way to prove a number, so the use
    case that needs this refuses rather than degrading - which makes
    ``describe_termii_configuration`` load-bearing rather than a courtesy. It is
    what turns "the SMS is unavailable" into "TERMII_API_KEY is not set".
    """

    def test_both_variables_are_read(self):
        settings = termii_from_environment(
            {"TERMII_API_KEY": "TL_test_abc", "TERMII_SENDER_ID": "BudgetMgr"}
        )

        assert settings.api_key == "TL_test_abc"
        assert settings.sender_id == "BudgetMgr"

    def test_no_key_means_no_settings(self):
        assert termii_from_environment({"TERMII_SENDER_ID": "BudgetMgr"}) is None

    def test_no_sender_id_means_no_settings(self):
        """**Half a configuration is none of a configuration**, which is where
        this reader differs from the two above it.

        A key with no sender id describes a request Termii refuses, and a settings
        object holding it would push the failure to send time - where the only
        available sentence is the provider's. ``None`` is the honest answer: this
        install cannot text anybody.
        """
        assert termii_from_environment({"TERMII_API_KEY": "TL_test_abc"}) is None

    def test_an_empty_string_counts_as_absent(self):
        """The variable somebody exported while clearing it."""
        assert (
            termii_from_environment(
                {"TERMII_API_KEY": "", "TERMII_SENDER_ID": "BudgetMgr"}
            )
            is None
        )

    def test_whitespace_counts_as_absent(self):
        assert (
            termii_from_environment(
                {"TERMII_API_KEY": "   ", "TERMII_SENDER_ID": "BudgetMgr"}
            )
            is None
        )

    def test_surrounding_whitespace_is_trimmed(self):
        """A sender id pasted out of a dashboard can carry a newline, and a sender
        id with one in it is a different string to the provider than the one that
        was registered."""
        settings = termii_from_environment(
            {"TERMII_API_KEY": " TL_test_abc\n", "TERMII_SENDER_ID": " BudgetMgr "}
        )

        assert settings.api_key == "TL_test_abc"
        assert settings.sender_id == "BudgetMgr"

    def test_the_missing_variable_is_named(self):
        """The property that makes this reader worth having rather than a ``None``.

        A misconfigured notifier fails by being silent, and which variable is
        missing is the difference between a two-minute fix and an afternoon. The
        key is reported before the sender id because it is the one an operator sets
        first - and the order is asserted rather than incidental.
        """
        assert describe_termii_configuration({}) == "TERMII_API_KEY is not set"
        assert (
            describe_termii_configuration({"TERMII_API_KEY": "TL_test_abc"})
            == "TERMII_SENDER_ID is not set"
        )

    def test_a_complete_configuration_has_no_reason_to_give(self):
        assert (
            describe_termii_configuration(
                {"TERMII_API_KEY": "TL_test_abc", "TERMII_SENDER_ID": "BudgetMgr"}
            )
            is None
        )

    def test_it_is_not_a_mail_setting_and_does_not_depend_on_one(self):
        """Stated in both directions, as the payment reader's version is.

        An install configured for mail cannot text, and an install configured for
        texts cannot mail - which is what makes it correct for the reset flow to
        ask about the channel the request *named* rather than about whether any
        channel is configured at all.
        """
        assert termii_from_environment(environment()) is None
        assert (
            from_environment(
                {"TERMII_API_KEY": "TL_test_abc", "TERMII_SENDER_ID": "BudgetMgr"}
            )
            is None
        )

