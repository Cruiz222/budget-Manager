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

``password_resets`` was the newest module before ``phone_verifications``, and it is
the first whose two paths are under prefixes that name no resource the caller owns.
Every other file here answers "whose is this?" somewhere in its paths -
``/users/me``, ``/wallets/{id}``, ``/sessions/current``. This one cannot, because
the caller is a person who cannot log in: the account is inferred from an address
and the mail goes to whatever mailbox that address names. So the paths are bare
plurals, and the absence of a ``/me`` is a true statement about the feature rather
than a router that forgot to say whose it was.

``profiles`` is beside ``users`` rather than inside it, and the split is the same
one the schema makes between a ``User`` and a ``Profile``: an account and the
person holding it are two things with two lifetimes, and the second is the one
that must not be loaded by every authenticated request. The two files share a
``tags`` label and nothing else - ``users`` answers "which account is this", which
is security-shaped, and ``profiles`` answers "who is this person", which is
compliance-shaped.

``phone_verifications`` is the newest module and the second whose paths name no
resource the caller owns - with ``password_resets`` it is one of exactly two such
files, and the reason is one step further out. There the caller cannot log in;
here the caller may not have an account to log into, because the account is what
answering the code creates. It is also the only module in the API whose request
half **costs the installation money per call**, which is worth knowing when
somebody comes looking for what an unauthenticated stranger can make this server
do: the answer is one text message, billed, per request.

The Google pair is the first feature here that gets **no module of its own**, and
the reason is the split this file is built on rather than convenience.
``POST /users/google`` and ``POST /sessions/google`` are the same two acts
``users`` and ``sessions`` already own - create an identity, and turn one into a
token - reached by a third kind of proof. A ``google`` module would therefore have
had to answer "whose is this?" twice for two routes whose answers are the two files
that already exist, and what it bought would be nothing while what it cost is the
reader who looks for registration in ``users`` and finds most of it. The price of
the split is two docstrings in which "the only one" stopped being true, and that is
the same discipline the boundary test's list applies to routes: a claim about
counts is corrected in place rather than left standing next to the code that
falsified it.
"""
