import pytest
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

from ..chatbot.app import app

@pytest.fixture
def test_client():
    return TestClient(app)

def test_app_startup():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}