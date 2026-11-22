"""Indicadores por ventana y máquina de estados de alertas."""
from dataclasses import dataclass
from math import ceil, log2


def percentile95(values):
    if not values:
        return None
    return sorted(values)[ceil(0.95 * len(values)) - 1]


def jsd(a, b):
    p, q = (a, 1 - a), (b, 1 - b)
    middle = [(x + y) / 2 for x, y in zip(p, q)]
    return 0.5 * sum(x * log2(x / m) for dist in (p, q)
                     for x, m in zip(dist, middle) if x)


def summarize(rows, cfg, latency_limit=None, baseline_oov=None):
    canaries = [r for r in rows if r["kind"] == "canary"]
    healthy = [r for r in canaries if r["status"] == 200]
    probes = canaries + [r for r in rows if r["kind"] == "ready"]
    failed = sum(r["status"] != 200 for r in probes)
    sample = [r for r in rows if r["kind"] == "drift"
              and r["outcome"] in ("reconocida", "sin_vocabulario")]
    oov = sum(r["outcome"] == "sin_vocabulario" for r in sample)
    oov_rate = oov / len(sample) if sample else None
    slow = None
    if latency_limit is not None and healthy:
        slow = sum(r["http_ms"] > latency_limit for r in healthy)
    return {
        "probes": len(probes), "failed": failed,
        "latency_samples": len(healthy), "slow": slow,
        "availability": 1 - failed / len(probes),
        "availability_burn": (failed / len(probes)) / (1 - cfg["availability_slo"]),
        "http_p95_ms": percentile95([r["http_ms"] for r in healthy]),
        "inference_p95_ms": percentile95([r["inference_ms"] for r in healthy]),
        "latency_compliance": None if slow is None else 1 - slow / len(healthy),
        "latency_burn": None if slow is None else (slow / len(healthy)) / (1 - cfg["latency_slo"]),
        "labeled_correct": sum(r["label_ok"] for r in healthy),
        "drift_n": len(sample), "oov": oov, "oov_rate": oov_rate,
        "jsd_bits": jsd(oov_rate, baseline_oov) if
        len(sample) >= cfg["min_drift_samples"] and baseline_oov is not None else None,
        "expected_422_extra": sum(r["kind"] == "rejection" and r["status"] == 422 for r in rows),
    }


@dataclass
class AlertState:
    bad: int = 0
    clean: int = 0
    active: bool = False


class AlertEngine:
    RULES = {
        "availability": ("P1", "RB-01"),
        "latency": ("P2", "RB-01"),
        "drift": ("P2", "RB-02"),
    }

    def __init__(self, cfg):
        self.cfg = cfg
        self.states = {key: AlertState() for key in self.RULES}

    def evaluate(self, m):
        cfg = self.cfg
        available = m["probes"] >= cfg["min_probes"]
        outage = m["availability_burn"] >= cfg["availability_burn_critical"]
        signals = {
            "availability": outage if available else None,
            "latency": (m["latency_burn"] >= cfg["latency_burn_warning"]
                        if m["latency_samples"] >= cfg["min_probes"] else None),
            "drift": (m["jsd_bits"] >= cfg["drift_jsd_threshold_bits"]
                      if m["jsd_bits"] is not None else None),
        }
        events = []
        for key, violation in signals.items():
            state = self.states[key]
            # Un fallo de disponibilidad tiene prioridad sobre señales derivadas.
            if violation is None or (key != "availability" and outage):
                state.bad = state.clean = 0
                continue
            state.bad = state.bad + 1 if violation else 0
            state.clean = state.clean + 1 if not violation else 0
            if violation and state.bad >= cfg["consecutive_breaches"]:
                action = "duplicate_suppressed" if state.active else "opened"
                events.append(self.event(key, action))
                state.active = True
            elif state.active and state.clean >= cfg["consecutive_clean"]:
                events.append(self.event(key, "resolved"))
                state.active = False
        return events

    @classmethod
    def event(cls, key, action):
        severity, runbook = cls.RULES[key]
        return {"rule": key, "state": action, "severity": severity, "runbook": runbook}
