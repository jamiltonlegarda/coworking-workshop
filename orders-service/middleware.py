"""Lab 11: rate limiting, circuit breaker y retry con backoff para orders-service."""
import asyncio
import os
import time
from collections import defaultdict
from enum import Enum

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog-service:8001")
CATALOG_TIMEOUT = 5.0  # segundos maximos de espera por el catalogo


# =========================== 1. RATE LIMITER ===========================
class RateLimiter:
    """Ventana deslizante: maximo 100 solicitudes por IP cada 60 segundos."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Un registro independiente por IP: {"192.168.1.10": [t1, t2, ...]}
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.monotonic()
        # Conservar solo las solicitudes de los ultimos 60 segundos
        recent = [t for t in self.requests[client_ip] if now - t < self.window_seconds]
        if len(recent) >= self.max_requests:
            self.requests[client_ip] = recent
            return False  # solicitud 101: bloqueada
        recent.append(now)
        self.requests[client_ip] = recent
        return True


rate_limiter = RateLimiter(max_requests=100, window_seconds=60)

# El health check no cuenta para el limite (lo usan monitores y scripts).
RATE_LIMIT_EXEMPT_PATHS = {"/health"}


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path not in RATE_LIMIT_EXEMPT_PATHS:
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.is_allowed(client_ip):
            # En un middleware hay que devolver la respuesta: lanzar HTTPException aqui
            # no llega al manejador de FastAPI.
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests", "retry_after_seconds": 60},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)


# =========================== 2. CIRCUIT BREAKER ===========================
class CircuitState(str, Enum):
    CLOSED = "closed"        # normal: las llamadas pasan
    OPEN = "open"            # fallando: se bloquean las llamadas
    HALF_OPEN = "half_open"  # prueba de recuperacion


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold  # fallos seguidos para abrir
        self.recovery_timeout = recovery_timeout    # segundos antes de probar de nuevo
        self.failures = 0
        self.last_failure_time: float | None = None
        self.state = CircuitState.CLOSED

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True  # deja pasar una prueba
            return False
        return True  # HALF_OPEN: prueba de recuperacion

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.monotonic()
        # Si la prueba de recuperacion falla, se vuelve a abrir de inmediato.
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def snapshot(self) -> dict:
        retry_in = None
        if self.state == CircuitState.OPEN and self.last_failure_time is not None:
            retry_in = max(0, round(self.recovery_timeout - (time.monotonic() - self.last_failure_time)))
        return {
            "state": self.state.value,
            "consecutive_failures": self.failures,
            "failure_threshold": self.failure_threshold,
            "retry_in_seconds": retry_in,
        }


catalog_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)


# =========================== 3. RETRY WITH BACKOFF ===========================
class CatalogError(Exception):
    """Fallo transitorio del catalogo (red o 5xx): vale la pena reintentar."""


class CircuitOpenError(Exception):
    """El circuito esta abierto: no tiene sentido reintentar."""


async def retry_with_backoff(func, max_attempts: int = 3, initial_delay: float = 1.0,
                             retry_on: tuple = (Exception,)):
    """Ejecuta func(); si falla espera 1 s, luego 2 s... y reintenta.

    Tras el ultimo intento fallido relanza el ultimo error.
    """
    delay = initial_delay
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except retry_on as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            await asyncio.sleep(delay)
            delay *= 2
    raise last_error


# =========================== 4. LLAMADA AL CATALOGO ===========================
async def _catalog_attempt(space_id: int) -> httpx.Response:
    """Un intento de llamada, protegido por el circuit breaker."""
    if not catalog_breaker.can_execute():
        raise CircuitOpenError()
    try:
        async with httpx.AsyncClient(timeout=CATALOG_TIMEOUT) as client:
            response = await client.get(f"{CATALOG_URL}/spaces/{space_id}")
    except httpx.RequestError as exc:
        catalog_breaker.record_failure()
        raise CatalogError(str(exc)) from exc
    if response.status_code >= 500:
        catalog_breaker.record_failure()
        raise CatalogError(f"catalog responded {response.status_code}")
    # 200 y 404 son respuestas validas del catalogo: el servicio esta vivo.
    catalog_breaker.record_success()
    return response


async def call_catalog(space_id: int) -> dict:
    """Retry con backoff + circuit breaker. Devuelve el JSON del espacio."""
    try:
        response = await retry_with_backoff(
            lambda: _catalog_attempt(space_id), retry_on=(CatalogError,)
        )
    except CircuitOpenError:
        raise HTTPException(status_code=503, detail="Catalog service unavailable (circuit open)")
    except CatalogError:
        raise HTTPException(status_code=503, detail="Catalog service unavailable")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Space not found")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Unexpected response from catalog")
    return response.json()