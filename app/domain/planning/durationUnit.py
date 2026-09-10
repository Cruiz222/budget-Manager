from enum import Enum


class DurationUnit(Enum):
    """The unit a plan's term is counted in.

    Deliberately four units rather than one. "3 months" and "90 days" are not
    the same instruction: three months from 31 January ends on 30 April, while
    90 days from 31 January ends on 1 May. Collapsing months into a day count
    would force the user's intent through a conversion that loses it - and the
    loss would only ever show up at year boundaries, where nobody is looking.

    ``WEEKS`` earns its place the same way: "every two weeks for six weeks"
    is three runs, and saying "42 days" instead would be a translation the user
    never asked for.
    """

    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"
