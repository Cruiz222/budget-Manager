# What a deployment runs, which is `requirements.txt` and nothing else.
#
# The tag is 3.12 because that is what `pyvenv.cfg` in the development venv
# records - `python:3.12-slim` is the same interpreter the suite passes on, and
# a deployment that ran a different one would be the one place in this project
# where "it works" and "it works there" were allowed to mean different things.
# `slim` rather than the full image because nothing here compiles anything at
# build time: every dependency in requirements.txt ships a manylinux wheel.
FROM python:3.12-slim

# Dependencies first and alone, so the layer caches across every source change.
# A build that re-resolves pip because a line in `app.py` moved is a build that
# reaches the network to learn nothing.
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

WORKDIR /srv
COPY app /srv/app

# A non-root user, created before anything is chowned to it and after the
# dependencies so that nothing installed above is owned by it. Nothing in this
# application needs privilege: it opens one file, one socket and one outbound
# connection.
#
# The uid is fixed at 10001 rather than left to `useradd`, because the host-side
# bind-mounted `./data` has to be chowned to the same number - and a uid that
# varies per build is a uid nobody can write down in an install step.
RUN useradd --create-home --uid 10001 budget

# The directory that holds the database. **The directory matters as much as the
# file**, and that is why this is a directory and not just a path: SQLite writes
# its rollback journal beside the database, so the *directory* has to be
# writable or every unit of work fails on open. Created here and owned by the
# runtime user, so that a named volume mounted at /data inherits the right
# ownership rather than arriving root-owned and unwritable.
#
# It is `install -d` and not `mkdir`, because the two things this has to get
# right - the mode and the owner - are one flag each here and three commands
# otherwise. Note what this does *not* fix: a bind mount takes its ownership
# from the host directory, not from the image, which is why `compose.yaml` and
# the README both carry an `install -d -o 10001` for the host side.
RUN install -d -o budget -g budget /data

USER budget

# `--factory` because `create_app` is a function rather than a module-level
# `app`, which is the form `__main__.py` already documents as the one a
# deployment wants - "it can then add --host, --port, --workers and --reload
# without this file having to predict which of them somebody will need."
#
# **`--host 0.0.0.0` here is the deployment's explicit decision, made in the
# place that decision belongs.** `__main__.py` binds loopback and says so,
# because a process listening on every interface speaks plain HTTP; inside a
# compose network the only thing that can reach this port is the reverse proxy,
# and the proxy is what terminates TLS.
#
# **One worker, and the number is not an oversight.** `SqliteUnitOfWorkFactory
# .start` takes `BEGIN IMMEDIATE` on *every* unit deliberately, so two writers
# cannot disagree about a value one of them is about to write. That is a
# property worth more than throughput, and it also means extra workers would buy
# contention rather than concurrency: the second worker's first write waits for
# the first worker's unit to commit either way.
CMD ["uvicorn", "app.presentation.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
