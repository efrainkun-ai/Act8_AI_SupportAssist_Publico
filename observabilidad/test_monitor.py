"""Regresiones de observabilidad y alertamiento que cubren ambos runbooks."""
import json
from pathlib import Path

from observabilidad.monitor import AlertEngine, jsd, summarize

CFG = json.loads(Path(__file__).with_name("config.json").read_text())


def test_drift_binario():
    assert jsd(0, 0) == 0
    assert jsd(0, 1) == 1
    assert jsd(0.2, 0.8) == jsd(0.8, 0.2)


def test_alertas_persistentes_se_deduplican_y_cierran():
    engine = AlertEngine(CFG)
    slow = {"probes": 25, "availability_burn": 0, "latency_samples": 24,
            "latency_burn": 4, "jsd_bits": 0}
    assert engine.evaluate(slow) == []
    assert engine.evaluate(slow)[0]["state"] == "opened"
    assert engine.evaluate(slow)[0]["state"] == "duplicate_suppressed"
    clean = {**slow, "latency_burn": 0}
    assert engine.evaluate(clean) == []
    assert engine.evaluate(clean)[0]["state"] == "resolved"


def test_falta_de_servicio_prioriza_p1_y_no_mide_drift():
    engine = AlertEngine(CFG)
    outage = {"probes": 25, "availability_burn": 100, "latency_samples": 0,
              "latency_burn": None, "jsd_bits": None}
    assert engine.evaluate(outage) == []
    assert engine.evaluate(outage) == [
        {"rule": "availability", "state": "opened", "severity": "P1", "runbook": "RB-01"}
    ]


def test_rechazo_esperado_no_consume_presupuesto_de_disponibilidad():
    rows = [{"kind": "canary", "status": 200, "http_ms": 1, "inference_ms": 0.2,
             "label_ok": True}] * 24
    rows += [{"kind": "ready", "status": 200}]
    rows += [{"kind": "rejection", "status": 422, "outcome": "idioma_no_admitido"}] * 8
    m = summarize(rows, CFG, 25, 0)
    assert m["availability_burn"] == 0
    assert m["expected_422_extra"] == 8
    assert m["jsd_bits"] is None
