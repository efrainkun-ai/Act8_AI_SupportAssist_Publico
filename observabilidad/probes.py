"""Sondas representativas del contrato de Fase 2 y lotes sintéticos de drift."""
from time import perf_counter
import httpx

CANARIES = [
    ("Where is my refund?", 1),
    ("How can I get my invoice?", 1),
    ("I have a payment issue", 1),
    ("I forgot my password", 0),
    ("I want to create an account", 0),
    ("Where is my order?", 0),
]
UNKNOWN = ["zxqvop qwzjft", "qwxjz yxqzjk", "jxzqq vvqzx", "zqkrx qxqvu"]


def request(client, path, *, text=None, language="en", expected_label=None, kind):
    start = perf_counter()
    row = {"kind": kind, "status": None, "outcome": "sin_respuesta",
           "trace_id": None, "model_version": None, "label_ok": None}
    try:
        if text is None:
            response = client.get(path)
        else:
            response = client.post(path, json={"text": text, "language": language})
        row["status"] = response.status_code
        row["outcome"] = response.headers.get("X-Input-Outcome")
        row["trace_id"] = response.headers.get("X-Request-ID")
        if response.status_code == 200:
            payload = response.json()
            row["model_version"] = payload.get("model_version")
            if expected_label is not None:
                row["label_ok"] = payload["label"] == expected_label
                row["inference_ms"] = payload["latency_ms"]
    except httpx.TransportError as error:
        row["transport"] = type(error).__name__
    row["http_ms"] = round(1000 * (perf_counter() - start), 3)
    return row


def measure(base_url, drift=False, outside_noise=False):
    with httpx.Client(base_url=base_url, timeout=0.8, trust_env=False) as client:
        observations = [request(client, "/ready", kind="ready")]
        observations += [
            request(client, "/api/v1/predict", text=text, expected_label=label, kind="canary")
            for text, label in CANARIES * 4
        ]
        tickets = [text for text, _ in CANARIES] * 4
        if drift:
            tickets[:16] = UNKNOWN * 4
        observations += [
            request(client, "/api/v1/predict", text=text, kind="drift")
            for text in tickets
        ]
        if outside_noise:
            observations += [
                request(client, "/api/v1/predict", text="Quiero un reembolso",
                        language="es", kind="rejection")
                for _ in range(8)
            ]
    return observations
