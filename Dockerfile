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

# **The runtime uid and gid are build arguments, and that is not
# configurability for its own sake - it is what makes the bind mount work
# without root.** A bind mount takes its ownership from the host directory, so
# the container user has to be the uid that already owns `./data`; the
# alternative is chowning the host directory to a fixed uid, and `chown` is
# root. Which is fine on a machine where you have root and a dead end on one
# where you do not - and a deployment host is the second kind more often than
# the first, because nobody should be running `docker compose` as root.
#
# So `compose.yaml` passes `id -u` and `id -g` from `.env`, the container
# process runs as the person who owns the directory, and `mkdir -p data` is the
# whole of the host-side setup.
#
# The defaults exist so that `docker build .` still works on its own, with no
# arguments to remember - a bare build gets a self-consistent image whose only
# flaw is that its uid will not match your host. `compose.yaml` deliberately
# does *not* fall back to them; it refuses, because a mismatched uid fails later
# as an unwritable database rather than immediately as a missing setting.
ARG BUDGET_UID=10001
ARG BUDGET_GID=10001

# A group and a user at those numbers, created before anything is chowned to
# them and after the dependencies so that nothing installed above is owned by
# them. Nothing in this application needs privilege: it opens one file, one
# socket and one outbound connection.
#
# `--no-user-group` and an explicit numeric `--gid` because the group already
# exists by the line above - without it, `useradd` would try to create a second
# group of the same name and fail the build.
RUN groupadd --gid "${BUDGET_GID}" budget \
 && useradd --uid "${BUDGET_UID}" --gid "${BUDGET_GID}" --no-user-group \
      --create-home budget

# The directory that holds the database. **The directory matters as much as the
# file**, and that is why this is a directory and not just a path: SQLite writes
# its rollback journal beside the database, so the *directory* has to be
# writable or every unit of work fails on open.
#
# It is `install -d` and not `mkdir`, because the two things this has to get
# right - the mode and the owner - are one flag each here and three commands
# otherwise. Note what this does *not* fix, and it is the whole reason for the
# arguments above: a bind mount takes its ownership from the host directory and
# ignores this one entirely.
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
