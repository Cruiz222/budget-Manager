from enum import Enum

#: The currencies this codebase can represent, and the whole of the list.
#:
#: **What this list is *not* is the menu.** Decision 268 narrowed the currencies
#: this installation will open a wallet in to ``NGN`` alone, and that set lives
#: beside the door that enforces it - ``wallet_service.OFFERED_CURRENCIES`` -
#: rather than here. The two are one layer apart on purpose: representing a
#: currency and selling one are different statements, and only the second is a
#: product decision. ``USD`` is on this list and is not on that one.
#:
#: **Two, and there used to be five.** ``GHS``, ``KES`` and ``EUR`` were removed
#: when the one-wallet-per-currency rule was made: a currency on this list is one
#: more row in ``tier.py``'s limits table, one more option in the web dropdown,
#: one more entry in the CLI's ``choices``, and one more currency a person could
#: put money into and find that nothing could take it out again - because the
#: payment rail collects NGN only.
#:
#: So that trim was not tidiness. It is the same statement ``SUPPORTED_CURRENCIES``
#: makes at the deposit door, moved one step earlier: rather than representing a
#: currency the system cannot serve and refusing it at one of its doors, the list
#: no longer contains it. Money in GHS is not a value this codebase can hold.
#:
#: **``USD`` was not removed with them, and decision 268 moved the line instead.**
#: Narrowing the offer to ``NGN`` was the product's to do and could have been done
#: here a second time. It was not, because a list with one member cannot express a
#: currency *mismatch*: adding naira to dollars, a wallet refusing a deposit in a
#: currency it does not hold, a plan paying in a currency its wallet cannot - all
#: of those become statements this codebase cannot write down, and the guards that
#: catch them become branches no test can reach. What the code can represent and
#: what the product sells were separated, rather than letting the second delete
#: the first. The set that decides the offer is ``OFFERED_CURRENCIES``, and it is
#: the second of three: this list, then that one, then the rail's.
#:
#: **What the GHS trim costs, said plainly rather than left to be discovered.**
#: The money domain is meant to be the part that does not know about rails, and
#: that trim is it knowing about one. A future installation with a GHS-capable
#: rail would add the member back and take the same three edits in the other
#: direction. What it buys is ``CurrencyNotCollectableError``'s one live case: a
#: USD wallet at an NGN-only rail - a wallet in a currency the list *does* carry
#: and this account cannot collect. That is a genuinely different situation from a
#: currency the codebase has never heard of, which is why it still needs a name.
class Currency(Enum):
    NGN = "NGN"
    USD = "USD"
