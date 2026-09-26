import pytest
from paperspeak import config, db


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    db.init()
    return tmp_path


@pytest.fixture
def client(database):
    from fastapi.testclient import TestClient
    from paperspeak.api import app, credentials, login_attempts

    login_attempts.clear()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + credentials()["api_key"]
        yield c
