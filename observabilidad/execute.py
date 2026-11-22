"""Ejercicio reproducible sobre un proceso HTTP aislado, con registro MLflow."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import httpx

from .monitor import AlertEngine, percentile95, summarize
from .probes import measure

# El seguimiento usa solo SQLite y artefactos locales.
os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
import mlflow

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "servicio"
CONFIG = ROOT / "observabilidad" / "config.json"


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                       encoding="utf-8")
    pending.replace(path)


def append_json(path, data):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")


class LocalService:
    def __init__(self, log, telemetry):
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            self.port = address.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.telemetry = telemetry
        self.log = log
        self.process = None

    def start(self, package, *, expect_ready=True):
        self.stop()
        env = dict(os.environ, MODEL_DIR=str(package), PYTHONUNBUFFERED="1",
                   SUPPORTASSIST_TELEMETRY=str(self.telemetry), OPENBLAS_NUM_THREADS="1",
                   OMP_NUM_THREADS="1")
        with self.log.open("a", encoding="utf-8") as stream:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", str(self.port), "--no-access-log"],
                cwd=SERVICE, env=env, stdout=stream, stderr=subprocess.STDOUT,
            )
        if not expect_ready:
            if self.process.wait(timeout=15) == 0:
                raise RuntimeError("El paquete inválido no provocó la falla esperada.")
            return
        with httpx.Client(timeout=0.3, trust_env=False) as client:
            until = monotonic() + 15
            while monotonic() < until and self.process.poll() is None:
                try:
                    if client.get(self.url + "/ready").status_code == 200:
                        return
                except httpx.TransportError:
                    sleep(0.08)
        raise RuntimeError("El servicio no quedó disponible. Revisar servicio.log.")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=4)


class Exercise:
    def __init__(self, destination, service, gap):
        self.out = destination
        self.service = service
        self.cfg = read_json(CONFIG)
        self.engine = AlertEngine(self.cfg)
        self.gap = gap
        self.windows, self.alerts, self.audit = [], [], []
        self.latency_limit = self.baseline_oov = None
        self.review_items = []

    def event(self, incident, step, **details):
        event = {"utc": utc(), "incident": incident, "step": step, **details}
        self.audit.append(event)
        append_json(self.out / "acciones.jsonl", event)

    def snapshot(self):
        write_json(self.out / "estado.json", {
            "updated": utc(), "slo": self.cfg, "latency_limit_ms": self.latency_limit,
            "baseline_oov": self.baseline_oov, "windows": self.windows,
            "alerts": self.alerts, "actions": self.audit,
        })

    def window(self, phase, *, drift=False, noise=False, evaluate=True):
        idx = len(self.windows) + 1
        records = measure(self.service.url, drift=drift, outside_noise=noise)
        for row in records:
            append_json(self.out / "sondas.jsonl", {"window": idx, "phase": phase, **row})
            if drift and row["kind"] == "drift" and row["outcome"] == "sin_vocabulario":
                self.review_items.append({"window": idx, "trace_id": row["trace_id"]})
        metrics = summarize(records, self.cfg, self.latency_limit, self.baseline_oov)
        metrics.update({"window": idx, "phase": phase, "utc": utc()})
        self.windows.append(metrics)
        events = self.engine.evaluate(metrics) if evaluate else []
        for event in events:
            event.update({"window": idx, "utc": utc(), "phase": phase})
            self.alerts.append(event)
            append_json(self.out / "alertas.jsonl", event)
        self.window_at = monotonic()
        self.snapshot()
        mlflow.log_metrics({k: v for k, v in metrics.items()
                            if isinstance(v, (int, float))}, step=idx)
        print(f"Ventana {idx:02d} · {phase:<22} · disponibles "
              f"{metrics['probes'] - metrics['failed']}/{metrics['probes']} · alertas {events}", flush=True)
        sleep(self.gap)
        return metrics, events

    def run(self, stable, candidate):
        self.service.start(stable)
        for _ in range(self.cfg["baseline_windows"]):
            self.window("línea base", evaluate=False)
        base_samples = [row["http_ms"] for line in (self.out / "sondas.jsonl").read_text(
            encoding="utf-8").splitlines() if (row := json.loads(line))["kind"] == "canary"
            and row["status"] == 200]
        if sum(w["failed"] for w in self.windows):
            raise RuntimeError("La línea base no pasó las sondas; se detiene la simulación.")
        p95 = percentile95(base_samples)
        self.latency_limit = max(self.cfg["latency_floor_ms"],
                                 round(p95 * self.cfg["latency_baseline_factor"], 3))
        self.baseline_oov = sum(w["oov"] for w in self.windows) / sum(w["drift_n"] for w in self.windows)
        mlflow.log_params({"latency_limit_ms": self.latency_limit, "baseline_oov": self.baseline_oov})
        report = {"baseline_http_p95_ms": p95, "baseline_canaries": len(base_samples),
                  "latency_limit_ms": self.latency_limit}
        self.window("control 422", noise=True)
        report["RB-01"] = self.rollback(stable, candidate)
        report["RB-02"] = self.drift_response()
        if any(s.active for s in self.engine.states.values()):
            raise RuntimeError("Quedaron alertas abiertas al finalizar.")
        return report

    def rollback(self, stable, candidate):
        start = monotonic()
        self.event("RB-01", "desplegar candidato de prueba")
        self.service.start(candidate, expect_ready=False)
        self.event("RB-01", "arranque rechazado por integridad")
        detected = None
        for _ in range(3):
            _, events = self.window("incidente integridad")
            if any(e["state"] == "opened" and e["rule"] == "availability" for e in events):
                detected = self.window_at
        if detected is None:
            raise RuntimeError("La alerta de disponibilidad no se abrió.")
        try:
            meta = read_json(stable / "metadata.json")
            if sha256((stable / "financial_nb.joblib").read_bytes()).hexdigest() != meta["model_sha256"]:
                raise RuntimeError("Se detiene la reversión: el candidato estable no coincide con su huella.")
            self.event("RB-01", "rollback automático", stable_sha256=meta["model_sha256"],
                       model_version=meta["model_version"])
            self.service.start(stable)
            for _ in range(self.cfg["consecutive_clean"]):
                m, _ = self.window("validación rollback")
                if m["failed"] or m["labeled_correct"] != m["latency_samples"]:
                    raise RuntimeError("Fallaron las sondas de recuperación.")
            if self.engine.states["availability"].active:
                raise RuntimeError("La alerta de disponibilidad sigue activa.")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            self.event("RB-01", "escalar a guardia técnica")
            raise
        restored = self.window_at
        self.event("RB-01", "recuperación validada", successful_probes=m["probes"])
        return {"detect_s": round(detected - start, 3), "recover_s": round(restored - detected, 3)}

    def drift_response(self):
        start = monotonic()
        self.event("RB-02", "inyectar lote sintético fuera de vocabulario")
        detected = None
        for _ in range(3):
            _, events = self.window("incidente drift", drift=True)
            if any(e["state"] == "opened" and e["rule"] == "drift" for e in events):
                detected = self.window_at
        if detected is None:
            raise RuntimeError("La alerta de drift no se abrió.")
        self.event("RB-02", "lote aislado para revisión humana",
                   responsible="supervisor de soporte", operation="excluir lote sintético del flujo normal",
                   criterion="422 sin_vocabulario; sin aplicar sugerencia de clasificación")
        write_json(self.out / "escalamiento.json", {"queue": "revision_humana",
                   "destination": "supervisor de soporte simulado", "count": len(self.review_items),
                   "requests": self.review_items, "reason": "drift de cobertura léxica"})
        for _ in range(self.cfg["consecutive_clean"]):
            m, _ = self.window("validación drift")
            if m["failed"] or m["labeled_correct"] != m["latency_samples"]:
                self.event("RB-02", "escalar a responsable de modelo")
                raise RuntimeError("Falló la validación tras aislar el lote.")
        if self.engine.states["drift"].active:
            raise RuntimeError("La alerta de drift sigue activa.")
        restored = self.window_at
        self.event("RB-02", "recuperación validada", jsd_bits=m["jsd_bits"])
        return {"detect_s": round(detected - start, 3), "recover_s": round(restored - detected, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evidencia" / "nueva_ejecucion")
    parser.add_argument("--interval", type=float, default=2, help="Pausa entre lotes, en segundos.")
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("--interval debe ser mayor o igual a cero")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "estado.json").exists() or (out / "mlflow.sqlite").exists():
        parser.error("Indica una carpeta nueva para conservar la ejecución previa.")
    service = LocalService(out / "servicio.log", out / "telemetria.jsonl")
    mlflow.set_tracking_uri("sqlite:///" + (out / "mlflow.sqlite").as_posix())
    experiment_name = "Actividad 8 AI-SupportAssist"
    mlflow.create_experiment(experiment_name, artifact_location=(out / "mlflow_artifacts").as_uri())
    mlflow.set_experiment(experiment_name)
    exercise = Exercise(out, service, args.interval)
    stable = SERVICE / "models"
    with TemporaryDirectory(prefix="supportassist-") as sandbox:
        candidate = Path(sandbox) / "candidate"
        shutil.copytree(stable, candidate)
        # Paquete inválido controlado en una copia temporal.
        (candidate / "financial_nb.joblib").write_bytes(b"paquete de prueba omitido")
        with mlflow.start_run(run_name="game-day-actividad-8") as run:
            try:
                model = read_json(stable / "metadata.json")
                mlflow.log_params({**exercise.cfg, "model_version": model["model_version"],
                                   "model_sha256": model["model_sha256"], "threshold": model["threshold"],
                                   "window_gap_s": args.interval, "python": sys.version.split()[0]})
                result = exercise.run(stable, candidate)
                result["run_id"] = run.info.run_id
                result["received_windows"] = len(exercise.windows)
                result["completed_utc"] = utc()
                write_json(out / "resumen.json", result)
                for incident in ("RB-01", "RB-02"):
                    mlflow.log_metrics({f"{incident}_{k}": v for k, v in result[incident].items()})
            finally:
                service.stop()
                for path in sorted(out.iterdir()):
                    if path.suffix in {".json", ".jsonl", ".log"}:
                        mlflow.log_artifact(str(path))
                mlflow.log_artifact(str(CONFIG))
        print(f"MLflow run_id {run.info.run_id}")


if __name__ == "__main__":
    main()
