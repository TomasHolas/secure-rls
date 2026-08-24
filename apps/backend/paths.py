"""Where the backend's state files live - the one module that derives a state path.

Every SQLite file the process writes sits in one **data directory**, so a deployment can keep
it outside the container image and a rebuild replaces code without touching data (issue #125).
The directory is configuration, not a literal in a handler: `SECURE_RLS_DATA_DIR` names it and
its default is this package's own directory, so the dev path (`uv run uvicorn app:app`) needs
no environment variable at all and the files stay exactly where they have always been. The
image sets the variable to a path compose mounts a named volume on (ADR 0013).

It is a directory rather than one variable per file because the five stores are one unit of
state: they are backed up, mounted and reset together, and a partial override - a persisted
audit log beside a discarded conversation registry - is a configuration nobody wants.

Only the *root* is resolved here, and only for files the process writes. Two derivations
deliberately stay where they are:

- `audit.db` and `vectors.db` are derived by `db.py` as siblings of whatever database it was
  handed (`db_path.with_name(...)`), which is a relation between files, not a location. That
  keeps a test or an eval that passes a tmp database from writing its audit trail and its
  vectors into the real data directory. It also settles `employees.db`: it lives in the data
  directory with its two satellites, so there is exactly one rule about where they sit.
- Committed inputs - `employees.csv`, `poisoned_manifest.json`, `runtime.json` - are read
  beside the module that reads them. They ship with the code and are never written.

The directory is created when missing, because every path below it is a file some module has
to be able to open; a missing directory would otherwise surface as an unopenable database.
"""

import os
from pathlib import Path

DATA_DIR_ENV_VAR = "SECURE_RLS_DATA_DIR"
BACKEND_DIR = Path(__file__).resolve().parent


def _resolve_data_dir() -> Path:
    """The configured data directory, defaulting to this package's directory, created if new."""
    configured = os.environ.get(DATA_DIR_ENV_VAR)
    directory = Path(configured).expanduser().resolve() if configured else BACKEND_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


DATA_DIR = _resolve_data_dir()

DB_PATH = DATA_DIR / "employees.db"
STATE_DB_PATH = DATA_DIR / "state.db"
CHECKPOINT_DB_PATH = DATA_DIR / "checkpoints.db"
