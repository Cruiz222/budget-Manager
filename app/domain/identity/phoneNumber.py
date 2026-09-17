"""What a phone number must look like before anything further down can use it.

**Two jobs, and they are the two ``emailAddress`` does one identifier over: a fold
that gives one number one spelling, and a shape rule that refuses the value which
is obviously not a number.**

The fold is load-bearing rather than tidy, and the argument is ``fold_email``'s
verbatim. ``08012345678``, ``+2348012345678`` and ``2348012345678`` are the same
number, so a ``UNIQUE`` column on a table with no fold is a constraint that does
not mean what it looks like it means: one person can hold three accounts, three
verifications can be pending against one handset, and the database is perfectly
happy about all of it. ``fold_email`` guarantees one spelling before the value
reaches the store because *that is the only place the guarantee can be made* -
the same reasoning, one identifier over.

**The canonical spelling is country-code-first, digits only, no ``+``.** That is
the international form every SMS provider wants on the wire, so the fold and the
provider's expectation agree *by construction* rather than by two rules that
happen to line up. ``TermiiSmsChannel`` sends what this function returned.

**There is no dialability grammar here, and that is a decision rather than a
stage of work.** ``InvalidProfilePhoneError`` already states the position - a
regex would "refuse the international formats it had not thought of" - and the
provider is now the authority that settles it. What this refuses is the value
that is plainly not a number, which is the role ``checked_email``'s ``@`` plays
and for the same reason: catching it here keeps a typo out of the table rather
than out of a login form.

**A usability rule of ``has_real_domain``'s kind is deliberately absent, and this
module says why rather than leaving a later reader to wonder.** That rule exists
because a live call supplied the evidence for it: ``live@localhost`` came back
``invalid_email_address`` where ``live@example.com`` returned a checkout URL, and
the difference was exactly one dot. No equivalent evidence exists for phones yet
- the integration below has not been run against the real provider - so there is
nothing to base a rule on but a guess, and a guess that refuses a legitimate
handset cannot be recovered from on the person's behalf. The rule arrives with
the live run that earns it, which is how the email one arrived.
"""

from .exception import InvalidUserPhoneError

#: The country code a bare national number is read as.
#:
#: **A decision, and it has to be one of those or the fold cannot exist at all.**
#: ``08012345678`` carries no information about which country it belongs to - the
#: trunk ``0`` is a national convention, not a dialling prefix - so a number
#: written that way is only foldable relative to somewhere. This is that
#: somewhere.
#:
#: It sits beside the NGN limits and the Naira amounts for the same reason: this
#: system's centre of gravity is Nigeria, its payment provider is Nigerian, and
#: its users overwhelmingly type the national form. A deployment elsewhere sets
#: this to its own code and the fold follows, which is why it is a named constant
#: rather than a literal buried in the function.
#:
#: Note the asymmetry the fold turns on: a number written in the *international*
#: form is never reinterpreted, so this default only ever applies to input that
#: did not say where it was from.
DEFAULT_COUNTRY_CODE = "234"

#: The characters a person types that mean nothing to a dialling plan.
#:
#: An explicit string rather than a regex or a ``str.isdigit`` sweep, and the
#: narrowness is the point - the same narrowness ``checked_email`` argues for.
#: These are separators every human writes and no numbering plan uses; anything
#: else that is not a digit is *not* silently discarded here, it is left for
#: ``checked_phone`` to refuse. That is what stops ``"call me on 0801..."``
#: folding cleanly into a number nobody can be reached at.
_SEPARATORS = " -()."

#: The longest a phone number may be, from the E.164 standard.
#:
#: Not a guess: 15 digits is the documented ceiling for an international number,
#: so a value longer than this cannot be one under any reading.
MAX_DIGITS = 15

#: The shortest a folded number may be.
#:
#: **A floor rather than a policy, and it exists only to catch truncation.** Every
#: national numbering plan in service has a subscriber part longer than this, so
#: no real number is refused by it - what it refuses is ``234`` on its own, or a
#: number somebody cut short while copying it. The upper bound is a standard and
#: this is a rough edge, which is why it refuses so little; the authority on
#: whether a number is dialable is the provider, exactly as it is for an address.
MIN_DIGITS = 8


def fold_phone(phone: str) -> str:
    """The one spelling of a number: country-code-first, digits only, no ``+``.

    A module function rather than a private step inside ``User``, which is
    ``fold_email``'s arrangement and its argument: ``UserRepository.find_by_phone``
    looks a number up *before* a ``User`` exists - deciding whether one exists is
    the question it is asking - so it cannot obtain the fold by constructing the
    aggregate. Two implementations of "the same number written two ways is one
    account" would be two chances to disagree, and the disagreement surfaces as a
    person unable to log in to an account that plainly exists.

    **The order of the four steps is the whole of the rule**, so it is worth
    reading them as one thing rather than four:

    1. *Separators go.* Nothing a dialling plan uses, everything a person types.
    2. *A leading ``+`` is a promise and is taken at face value.* It says the
       country code is already present, so ``+15417543010`` stays a US number
       instead of being read as a Nigerian one with a ``1`` on the front. This is
       the only reading under which the international form is stable, and it is
       why the ``+`` is handled before the country code rather than after.
    3. *A leading ``0`` is a trunk prefix and goes*, but only when there was no
       ``+`` - because ``+2340801...`` is a contradiction and the ``+`` wins.
    4. *The default country code is prepended if it is not already there*, which
       is what makes ``8012345678`` and ``2348012345678`` one value.

    Step 4 tests rather than always prepends, and that is not an optimisation: a
    number already carrying its country code would otherwise fold to
    ``234234...``, which is the same drift this function exists to remove,
    arriving from the opposite direction.

    **Step 4 is also skipped when there are no digits left at all**, which is the
    same care one step further out. Prepending the country code to nothing would
    turn ``"()"``, ``"+"`` and ``"   "`` into ``"234"`` - a *number*, and a
    three-digit one that no plan assigns - so a value that is not a number would
    arrive at ``checked_phone`` looking like a malformed one and be refused as
    too short. Returning ``""`` instead lets the emptiness rule refuse it as what
    it is, and it keeps this function's contract honest: it maps a number to its
    spelling, and ``""`` is the honest spelling of "no number here".

    Assumes a ``str``. The type check belongs to ``checked_phone``, which every
    minting site calls first, and to the repository, which guards its own lookup -
    the same division ``fold_email`` relies on.
    """
    digits = "".join(
        character for character in phone.strip() if character not in _SEPARATORS
    )

    # Steps 2 and 3. ``international`` is recorded rather than inferred later,
    # because by the time the country code is considered the ``+`` is gone and
    # there is nothing left to tell the two origins apart.
    international = digits.startswith("+")
    if international:
        digits = digits[1:]
    elif digits.startswith("0"):
        digits = digits[1:]

    # Step 4, skipped entirely for a number that named its own country - and for
    # one that has nothing left to prepend to. See the docstring.
    if not digits:
        return ""

    if not international and not digits.startswith(DEFAULT_COUNTRY_CODE):
        digits = DEFAULT_COUNTRY_CODE + digits

    return digits


def checked_phone(phone: str) -> str:
    """The number a ``User`` will hold, from the number it was handed.

    The whole of the aggregate's own rule - a string, folded, non-empty, digits,
    a plausible length - in one function rather than inline in the constructor,
    for the reason ``checked_email`` gives: it is a rule with more than one
    potential caller and two copies of it could disagree.

    **It has one caller today, and that is worth stating rather than implying.**
    ``User.__post_init__`` is it, which covers the way in *and the way back in*,
    since every repository constructs a ``User`` from its row. ``checked_email``'s
    second caller is ``User.change_email``, and there is no ``change_phone``
    beside it - moving a number between accounts is a flow this slice
    deliberately does not design, so this function is extracted on the strength
    of a second caller that is *coming* rather than one that exists. If that flow
    is never built, the extraction still cost nothing and the constructor is one
    line shorter.

    Returns the folded value rather than checking in place, for ``checked_email``'s
    reason: a caller has no way to hold a checked-but-unfolded number, and a
    function that checked and returned nothing would leave the fold to be
    remembered separately at each call site.

    The emptiness test runs on the *folded* value, so a value that folds to
    nothing - ``"()"``, ``"+"``, ``"   "``, ``"0"`` - is refused as empty rather
    than stored. That is ``fold_phone``'s ``""`` return doing the work, and it is
    the difference between refusing a blank as a blank and refusing it as a
    number that is too short.
    """
    if not isinstance(phone, str):
        raise InvalidUserPhoneError("invalid user phone")

    folded = fold_phone(phone)

    if not folded:
        raise InvalidUserPhoneError("phone must not be empty")

    # The digit test is the whole of the shape rule's character check, and it is
    # deliberately written as a membership test on a literal rather than
    # ``str.isdigit``. ``isdigit`` answers True for superscripts and for digits
    # from other numbering systems - ``"²"`` and ``"٤"`` among them - which would
    # be stored as a number that is not what anybody typed and cannot be dialled.
    if not all(character in "0123456789" for character in folded):
        raise InvalidUserPhoneError("phone must contain only digits")

    if len(folded) < MIN_DIGITS or len(folded) > MAX_DIGITS:
        raise InvalidUserPhoneError(
            f"phone must be between {MIN_DIGITS} and {MAX_DIGITS} digits"
        )

    return folded
