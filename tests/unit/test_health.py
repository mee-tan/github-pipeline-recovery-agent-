
# automated test 

from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)

# function to test the health check endpoint
def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    