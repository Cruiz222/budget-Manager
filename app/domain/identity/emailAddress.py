"""What an address must look like before anything further down can use it.

**One predicate, four call sites, and they do not all read it the same way.**
``has_real_domain`` answers a single question - is there a dot after the last
``@`` - and the answer is used as a **guard** at the three places this system
mints an address (``SignUp``, the email-change request, and ``SignUpWithGoogle``)
and as a **courtesy** at the one place an address is handed to somebody else
(``InitiateDeposit``, before a provider is asked to bill it).

The rule is the same rule, and the two readings are not the same thing:

  guard     a refusal is cheap. The person is standing at a form, holding the
            address they already use, and the remedy is to type a different one.
            This system usually knows better than they do at that moment, and
            nothing is lost by saying so.

  courtesy  a refusal can be wrong in a way that cannot be undone. The money is
            real, the person did nothing wrong, and the system cannot deposit on
            their behalf. So it refuses only what it can be *certain* about and
            hands everything else to the authority.

Decision 157 argued the courtesy's narrowness and that argument has not changed;
what changed is that the same narrow rule turned out to be the right entry rule
too, for the opposite reason. At sign-up a strict rule that guesses wrong costs a
retry; at deposit it costs a deposit. One predicate, read correctly at each site -
which is decision 143's shape one job over, and the reason this file exists rather
than a copy of the rule in each place.

**The third guard site is the odd one and the rule still applies to it.** A Google
sign-up does not read an address out of a form, so nothing is being *typed* there -
but the account is created there, holding whatever Google reported, and that is the
moment the address enters this system. A refusal costs the person a second Google
account rather than a retry, which is a real cost and the smaller of the two: the
alternative is an account whose address no payment provider will bill, discovered
at the first deposit. Note also that Google's address is not automatically a real
one under this rule - a Workspace account on an internal domain with no dot is
exactly the shape this refuses, and refusing it at the door is decision 157 read as
a guard rather than as a courtesy.

**It lives in ``identity`` rather than beside the payer check**, because what an
address *is* belongs to the aggregate that holds one (``User``), and the payments
package borrows the question rather than owning it. ``app.domain.payments.payerEmail``
is the one cross-package reader, and it delegates here rather than restating the
dot test: two implementations of "does this address have a domain" would be two
chances to disagree, and the disagreement would surface as an account this system
refuses to create and happily tries to bill, or the reverse.

**Not in a regular expression, and not a full address grammar.** The evidence below
supports exactly one character, so exactly one character is checked. A cleverer
test would refuse more, and every additional refusal is a guess about a provider
this system does not control and a person it cannot compensate.
"""

from .exception import UnusableEmailError


def has_real_domain(email: str) -> bool:
    """Whether ``email`` has a dot in its domain part.

    ``rpartition`` and not ``split``: an address may contain more than one ``@``
    (a quoted local part may), and the domain is what follows the *last* one. With
    ``split`` an address like ``"a@b@localhost"`` would report ``b`` as its domain
    and pass a check it should fail, because the domain a mail system reads is
    everything after the final separator.

    The ``at`` guard is what makes ``"nobody"`` answer ``False`` rather than
    raise: ``rpartition`` returns ``("", "", "nobody")`` when the separator is
    absent, so ``domain`` is the whole string and a bare ``"." in domain`` test
    would accept an address with no ``@`` at all. ``User`` refuses such a value
    long before here, but this function does not assume its caller - which is the
    mistake the reference rule made, and the reason it now lives in one place.

    Why a dot, and why nothing more. A live call carrying ``live@localhost``
    returned

    ```
    {"message":"Invalid Email Address Passed","code":"invalid_email_address"}
    ```

    while the same call with ``live@example.com`` returned a checkout URL. That
    pair is the whole of the evidence, and it differs by exactly one dot - so a
    dot is what is checked. It is no guarantee anybody will accept the address
    (``a@b.`` has a dot and is almost certainly unbillable), and that is precisely
    why the caller at the deposit site is a courtesy rather than the guard.
    """
    _, at, domain = email.rpartition("@")
    return bool(at) and "." in domain


def refuse_unusable_email(email: str) -> None:
    """Raise ``UnusableEmailError`` for an address this system will not hold.

    Returns nothing rather than a bool, which is ``app.domain.money.reference``'s
    shape and ``refuse_unusable_payer_email``'s beside it: a caller that has to
    *check* a return value can forget to, and the refusal is the whole of the
    contract.

    **Called after the aggregate's shape rule, never before it**, and the ordering
    is load-bearing rather than tidy. ``"not-an-address"`` has no ``@``, so it is
    refused here as well - but it must keep answering ``InvalidUserEmailError``,
    which is the rule the person can act on ("that is not an address") rather than
    this one ("that address cannot work"). Shape first, then usability, so each
    value is refused by the more specific rule it actually broke.

    What this deliberately does *not* do is decide anything by looking at a
    database. "Is this address taken?" is a different question with a different
    error (``DuplicateEmailError``), asked by the use case that has a store to ask.
    """
    if has_real_domain(email):
        return

    raise UnusableEmailError(
        f"{email!r} cannot be used as an email address; it needs a domain with a "
        f"dot in it, like 'you@example.com'"
    )
