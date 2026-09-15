"""One module per resource, each exporting a ``router``.

The split is by *what the paths are about* rather than by use case, so a reader
looking for an endpoint can guess the file and be right. ``funds`` is separate
from ``wallets`` for the same reason the paths are nested: a pot is reachable
only through the wallet that holds it, and the file layout says so. ``sessions``
is separate from ``users`` for a related reason: an account and the proof that a
request *is* that account have different lifetimes and different owners, and the
two endpoints that hand out and discard a token are the only pair in the API that
can be reached without already having one.

One thing the layout deliberately does *not* do is give any router a prefix.
Every path is written in full at the route that serves it, which is slightly
repetitive and buys two things: a reader can see the URL without concatenating a
prefix in their head, and the boundary this phase draws - which operations exist
and which do not - is legible by reading the files rather than by inferring it
from what is missing. ``tests/presentation/api/test_boundary.py`` asserts the
missing half.

``password_resets`` is the newest module and the first whose two paths are under
prefixes that name no resource the caller owns. Every other file here answers
"whose is this?" somewhere in its paths - ``/users/me``, ``/wallets/{id}``,
``/sessions/current``. This one cannot, because the caller is a person who cannot
log in: the account is inferred from an address and the mail goes to whatever
mailbox that address names. So the paths are bare plurals, and the absence of a
``/me`` is a true statement about the feature rather than a router that forgot to
say whose it was.
"""
