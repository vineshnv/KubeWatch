from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)

def test_lifecycle():
    res=client.post('/api/connect',json={'mode':'local'})
    assert res.status_code==200
    token=res.json()['token']
    headers={'X-Session':token}
    assert client.get('/api/diagnostics/overview',headers=headers).status_code==200
    assert client.get('/api/diagnostics/invalid',headers=headers).status_code==404
    assert client.post('/api/disconnect',headers=headers).status_code==200
    assert client.get('/api/diagnostics/overview',headers=headers).status_code==401

def test_unauthenticated():
    assert client.get('/api/diagnostics/pods').status_code==401
