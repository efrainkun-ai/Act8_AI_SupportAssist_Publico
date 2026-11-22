"""API y frontend bajo el mismo origen, sin claves ni servicios externos al inferir."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .inference import InferenceError, Predictor
from .telemetry import emit, observe

ROOT = Path(__file__).resolve().parents[1]


class Ticket(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: StrictStr = Field(min_length=1, max_length=4000, examples=['Where is my refund?'])
    language: StrictStr = Field(pattern=r'^(en|es)$', examples=['en'])


class Prediction(BaseModel):
    label: int
    route: str
    financial_score: float
    threshold: float
    model_version: str
    human_review_required: bool
    scope: str
    request_id: str
    latency_ms: float


def create_app(model_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        try:
            application.state.predictor = Predictor(model_dir or Path(os.environ.get('MODEL_DIR', ROOT / 'models')))
        except (OSError, ValueError, RuntimeError) as exc:
            emit(event='startup_failed', error_type=type(exc).__name__)
            raise
        emit(event='startup_ready', model_version=application.state.predictor.metadata['model_version'])
        yield

    application = FastAPI(
        title='AI-SupportAssist', version='2.0.0', lifespan=lifespan,
        description='Triaje financiero binario. Umbral 0.24. No autoriza pagos o reembolsos.',
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # No devolver el texto recibido en los detalles de validación.
        return JSONResponse(status_code=422, content={'detail': [
            {'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()
        ]})

    @application.middleware('http')
    async def body_limit(request: Request, call_next):
        # Contar el cuerpo real también protege solicitudes sin Content-Length.
        if request.method == 'POST':
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32768:
                    return JSONResponse(status_code=413, content={'detail': 'Solicitud demasiado grande.'})
            request._body = bytes(body)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'" if request.url.path in ['/', '/static/app.js', '/static/style.css'] else "frame-ancestors 'none'"
        return response

    @application.get('/health', tags=['Estado'])
    def health():
        return {'status': 'ok', 'version': '2.0.0'}

    @application.get('/ready', tags=['Estado'])
    def ready(request: Request):
        if not hasattr(request.app.state, 'predictor'):
            raise HTTPException(503, 'Modelo no disponible')
        return {'status': 'ready', 'model_version': request.app.state.predictor.metadata['model_version']}

    @application.get('/api/v1/model', tags=['Modelo'])
    def model_info(request: Request):
        meta = request.app.state.predictor.metadata
        return {k: meta[k] for k in ['model_version', 'algorithm', 'threshold', 'financial_categories', 'environment', 'scope']}

    @application.post('/api/v1/predict', response_model=Prediction, tags=['Inferencia'])
    def predict(ticket: Ticket, request: Request):
        start = perf_counter()
        try:
            result = request.app.state.predictor.predict(ticket.text, ticket.language)
            request.state.input_outcome = 'reconocida'
        except InferenceError as exc:
            request.state.input_outcome = exc.outcome
            raise HTTPException(422, str(exc)) from exc
        finally:
            duration_ms = round((perf_counter() - start) * 1000, 3)
            emit(event='span', trace_id=request.state.trace_id, span_id=uuid4().hex[:16],
                 parent_id=request.state.span_id, route='inference', duration_ms=duration_ms,
                 outcome=request.state.input_outcome)
        return {**result, 'request_id': str(UUID(hex=request.state.trace_id)), 'latency_ms': duration_ms}

    @application.get('/', include_in_schema=False)
    def index():
        return FileResponse(ROOT / 'app/static/index.html')

    application.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')
    application.middleware('http')(observe)
    return application


app = create_app()
