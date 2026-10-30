import os
import tempfile
from pathlib import Path

# These must be set BEFORE app.py is imported, so the tests use a temporary
# SQLite file instead of your real MySQL database.
_tmp_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(_tmp_dir) / 'test.db').as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402

from app import app as flask_app  # noqa: E402
from app import db  # noqa: E402


@pytest.fixture
def app():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def other_client(app):
    """A second browser, used to test that users cannot see each other's data."""
    return app.test_client()


def register(client, username="alice", password="password123", confirm=None):
    return client.post(
        "/register",
        data={
            "username": username,
            "password": password,
            "confirm": password if confirm is None else confirm,
        },
        follow_redirects=True,
    )


def login(client, username="alice", password="password123"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )