"""The FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from ai_models.exceptions import (
    AiModelsError,
    ArtifactError,
    DataError,
    DataNotFoundError,
    InsufficientDataError,
    ModelNotFoundError,
)
from fastapi import FastAPI, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .schemas import (
    SYMBOL_PATTERN,
    BacktestRequest,
    BacktestResponse,
    Health,
    HistoryResponse,
    ModelsResponse,
    PredictionOut,
    SignalsResponse,
    TrainRequest,
    TrainResponse,
)
from .service import (
    MAX_HISTORY_ROWS,
    AiService,
    TrainingBusyError,
    TrainingDisabledError,
)
from .settings import Settings

log = logging.getLogger("ai_services")

SymbolPath = Annotated[str, Path(pattern=SYMBOL_PATTERN, description="Ticker symbol")]

# Most specific first: the first matching entry wins.
_STATUS_BY_ERROR: list[tuple[type[Exception], int]] = [
    (ModelNotFoundError, 404),
    (DataNotFoundError, 404),
    (InsufficientDataError, 422),
    (TrainingBusyError, 409),
    (TrainingDisabledError, 403),
    (ArtifactError, 500),
    (DataError, 422),
    (AiModelsError, 500),
]


def _error(status: int, message: str) -> JSONResponse:
    """Errors use the C++ server's shape: {"error": "..."}."""
    return JSONResponse(status_code=status, content={"error": message})


def create_app(
    settings: Settings | None = None, service: AiService | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    service = service or AiService(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.auto_train:
            outcome = await run_in_threadpool(service.startup)
            log.info("startup training: %s", outcome)
        yield

    app = FastAPI(
        title="AlphaForge AI Service",
        version=__version__,
        description="Direction, volatility, regime and anomaly models for AlphaForge.",
        lifespan=lifespan,
        docs_url="/ai/docs",
        openapi_url="/ai/openapi.json",
        redoc_url=None,
    )
    app.state.service = service

    # ----------------------------------------------------------------- errors
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}"
            for e in exc.errors()
        )
        return _error(400, f"invalid request: {problems}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Unknown routes and wrong methods: same error shape as everything else.
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(AiModelsError)
    async def _domain(_: Request, exc: AiModelsError) -> JSONResponse:
        for kind, status in _STATUS_BY_ERROR:
            if isinstance(exc, kind):
                if status >= 500:
                    # Artifact errors name files; keep that in the log, not the response.
                    log.error("model error: %s", exc)
                    return _error(status, "model artifact could not be loaded")
                return _error(status, str(exc))
        return _error(500, "internal error")  # pragma: no cover

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return _error(500, "internal server error")

    # ----------------------------------------------------------------- routes
    @app.get("/ai/health", response_model=Health)
    def health() -> dict:
        return service.health()

    @app.get("/ai/models", response_model=ModelsResponse)
    def models() -> dict:
        return {"models": service.models()}

    @app.get("/ai/predict/{symbol}", response_model=PredictionOut)
    def predict(symbol: SymbolPath) -> dict:
        return service.predict(symbol)

    @app.get("/ai/signals", response_model=SignalsResponse)
    def signals() -> dict:
        return service.signals()

    @app.get("/ai/history/{symbol}", response_model=HistoryResponse)
    def history(
        symbol: SymbolPath,
        rows: Annotated[int, Query(ge=1, le=MAX_HISTORY_ROWS)] = 120,
    ) -> dict:
        return service.history(symbol, rows)

    @app.post("/ai/backtest", response_model=BacktestResponse)
    def backtest(request: BacktestRequest) -> dict:
        return service.backtest(
            request.symbol,
            request.transaction_cost_bps,
            request.slippage_bps,
            request.initial_capital,
            request.target_volatility,
            request.long_threshold,
        )

    @app.post("/ai/train", response_model=TrainResponse)
    def train(request: TrainRequest | None = None) -> dict:
        symbols = request.symbols if request is not None else None
        return {"trained": service.train(symbols)}

    return app
