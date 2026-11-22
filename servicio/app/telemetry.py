"""Eventos JSON sin cuerpos, habilitados mediante un archivo de salida local."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def emit(**event):
    target = os.environ.get("SUPPORTASSIST_TELEMETRY")
    if target:
        record = {"utc": datetime.now(timezone.utc).isoformat(), **event}
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def observe(request, call_next):
    trace_id = uuid4().hex
    request.state.trace_id = trace_id
    request.state.span_id = uuid4().hex[:16]
    request.state.input_outcome = "no_evaluada"
    status = 500
    started = perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = trace_id
        response.headers["X-Input-Outcome"] = request.state.input_outcome
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "sin_ruta")
        predictor = getattr(request.app.state, "predictor", None)
        model_version = predictor.metadata["model_version"] if predictor else None
        context = {
            "trace_id": trace_id, "route": path, "method": request.method,
            "status": status, "outcome": request.state.input_outcome,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "model_version": model_version,
        }
        emit(event="request", **context)
        emit(event="span", span_id=request.state.span_id, parent_id=None, **context)
