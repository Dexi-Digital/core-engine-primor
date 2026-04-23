from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_module_status_endpoints() -> None:
    client = TestClient(app)
    for prefix, module in [
        ("dp-sesmt", "dp_sesmt"),
        ("manutencao-frota", "manutencao_frota"),
        ("financeiro", "financeiro_contratos"),
        ("licitacoes", "licitacoes"),
        ("ia", "ia_tools"),
    ]:
        resp = client.get(f"/api/v1/{prefix}/status")
        assert resp.status_code == 200, prefix
        body = resp.json()
        assert body["module"] == module
        assert body["stub"] is True
