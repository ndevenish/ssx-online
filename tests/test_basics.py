import pytest
from fastapi.testclient import TestClient

import ssx_online.fast


@pytest.fixture
def client(ispyb):
    # os.environ["JWT_SECRET"] = "SECRET_CODE"
    return TestClient(ssx_online.fast.app)


def test_root_redirect(client):
    response = client.get("/")
    assert response.url.endswith("/docs")
    assert response.history


def test_get_visits(client):
    response = client.get("/visits")
    response.raise_for_status()
    assert any(x["code"] == "mx24447-95" for x in response.json())
