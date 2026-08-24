"""The data directory: one owner for every state path, defaulting to today's (issue #125)."""

import importlib
from pathlib import Path

import pytest

import db
import paths


@pytest.fixture(autouse=True)
def restore_paths():
    """Reload the module after any test that reimported it under a patched environment."""
    yield
    importlib.reload(paths)


def test_the_default_data_directory_is_the_package_itself():
    """The dev path needs no environment variable: unset means where the files already sat."""
    assert paths.DATA_DIR == Path(__file__).resolve().parent.parent
    assert paths.DB_PATH == paths.BACKEND_DIR / "employees.db"
    assert paths.STATE_DB_PATH == paths.BACKEND_DIR / "state.db"
    assert paths.CHECKPOINT_DB_PATH == paths.BACKEND_DIR / "checkpoints.db"


def test_every_state_path_sits_in_the_one_data_directory():
    """One directory is the unit of state - it is mounted, backed up and reset as a whole."""
    for path in (paths.DB_PATH, paths.STATE_DB_PATH, paths.CHECKPOINT_DB_PATH):
        assert path.parent == paths.DATA_DIR


def test_the_environment_moves_every_state_path_together(monkeypatch, tmp_path):
    """A deployment points the whole set at a volume with one variable."""
    monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(tmp_path / "state"))
    reloaded = importlib.reload(paths)
    assert reloaded.DATA_DIR == tmp_path / "state"
    assert reloaded.DB_PATH == tmp_path / "state" / "employees.db"
    assert reloaded.STATE_DB_PATH == tmp_path / "state" / "state.db"
    assert reloaded.CHECKPOINT_DB_PATH == tmp_path / "state" / "checkpoints.db"


def test_a_configured_directory_that_does_not_exist_is_created(monkeypatch, tmp_path):
    """Every path below it is a file some module must open, so a missing directory is made."""
    target = tmp_path / "fresh" / "nested"
    monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(target))
    reloaded = importlib.reload(paths)
    assert reloaded.DATA_DIR == target
    assert target.is_dir()


def test_the_audit_and_vector_stores_follow_the_database_they_were_handed(tmp_path):
    """The satellites follow the database handed in, so a tmp database keeps its own pair."""
    handed = tmp_path / "employees.db"
    assert db._audit_path(handed) == tmp_path / db.AUDIT_DB_NAME
    assert db._vector_path(handed) == tmp_path / db.VECTOR_DB_NAME
    assert db._audit_path(paths.DB_PATH).parent == paths.DATA_DIR
    assert db._vector_path(paths.DB_PATH).parent == paths.DATA_DIR
