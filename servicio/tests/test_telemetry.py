import json
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app


def test_trazas_correlacionadas_sin_texto_en_exitos_y_rechazos(monkeypatch, tmp_path):
    target = tmp_path / "telemetry.jsonl"
    monkeypatch.setenv("SUPPORTASSIST_TELEMETRY", str(target))
    text = "zxqvop qwzjft"
    with TestClient(create_app()) as client:
        rejected = client.post("/api/v1/predict", json={"text": text, "language": "en"})
        accepted = client.post("/api/v1/predict", json={"text": "Where is my refund?", "language": "en"})
    assert rejected.status_code == 422
    assert rejected.headers["X-Input-Outcome"] == "sin_vocabulario"
    assert UUID(accepted.json()["request_id"]).hex == accepted.headers["X-Request-ID"]
    events = target.read_text()
    assert text not in events
    for response in (rejected, accepted):
        trace_id = response.headers["X-Request-ID"]
        linked = [event for line in events.splitlines() if (event := json.loads(line)).get("trace_id") == trace_id]
        child = next(e for e in linked if e["route"] == "inference")
        root = next(e for e in linked if e["event"] == "span" and e["route"] == "/api/v1/predict")
        assert child["parent_id"] == root["span_id"]
        assert any(e["event"] == "request" and e["status"] == response.status_code for e in linked)
