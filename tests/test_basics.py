from pprint import pprint

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
    code = "mx24447-95"
    matched_visit = [x for x in response.json() if x["code"] == code]
    assert len(matched_visit) == 1
    assert matched_visit[0]["url"].endswith(f"/visits/{code}")


def test_get_visit(client):
    response = client.get("/visits/mx24447-95")
    response.raise_for_status()
    visit = response.json()
    assert visit["DataCollections"]
    assert any(x["dataCollectionId"] == 9120230 for x in visit["DataCollections"])


def test_get_dc(client):
    response = client.get("/dc/9120230")
    response.raise_for_status()
    pprint(response.json())
