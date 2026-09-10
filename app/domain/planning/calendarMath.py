"""Calendar arithmetic shared by the two concepts that need it.

A ``Schedule`` moves a moment forward by *repeating* steps; a ``Duration`` moves
that moment forward once, to a term's end. They do not otherwise know about each
other - but they both have to add whole months, and they both have to survive
February.

That makes the arithmetic a concept neither of them owns. It gets its own module
rather than one importing the other for a helper it has no business knowing
about: a ``Duration`` depending on ``Schedule`` would be a dependency that says
something false about the domain.
"""

import calendar
from datetime import date
from typing import TypeVar

MONTHS_PER_YEAR = 12

#: Either a ``date`` or a ``datetime`` - whichever the caller handed in comes
#: back. ``datetime`` subclasses ``date``, so ``bound=date`` admits both while
#: still letting a type checker follow which one it was.
DateT = TypeVar("DateT", bound=date)


def add_months(start: DateT, months: int) -> DateT:
    """``start`` moved forward by whole months, clamped to the target month's end.

    The clamp is what makes February survivable: a plan anchored on the 31st
    lands on the 28th in a short month. Note that the day is always read from
    ``start`` - the anchor - and never from the previous occurrence. That is the
    whole trick. Advancing month-by-month from the *last run* would take
    31 Jan -> 28 Feb -> 28 Mar and never find its way back to the 31st.

    ``replace`` rather than ``date(year, month, day)``, and that is a change of
    behaviour rather than a tidy-up. Rebuilding the value silently discarded the
    time of day, so a plan anchored at noon on the 31st came back at midnight on
    the 28th and - because every occurrence is derived from the anchor - stayed
    at midnight for the rest of its life. ``replace`` writes only the three
    fields it is given and leaves the rest alone, so a ``datetime`` carries its
    time through the clamp and a ``date``, which has no time to carry, is
    untouched.
    """
    index = start.month - 1 + months
    year = start.year + index // MONTHS_PER_YEAR
    month = index % MONTHS_PER_YEAR + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    day = min(start.day, last_day_of_month)
    return start.replace(year=year, month=month, day=day)
