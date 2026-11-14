from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

import mongodb_client as mc


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crea los indices al arrancar; si Atlas no responde, el servicio igual levanta.
    try:
        mc.ensure_indexes()
        print("Indices de MongoDB verificados")
    except PyMongoError as exc:
        print(f"No se pudieron crear los indices todavia: {type(exc).__name__}")
    yield


app = FastAPI(title="Analytics Service", lifespan=lifespan)


class MetricIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: float
    tags: dict[str, str] = {}


class LogIn(BaseModel):
    level: str = Field(pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    message: str = Field(min_length=1, max_length=1000)
    service: str = "orders-service"


def db_unavailable():
    return HTTPException(status_code=503, detail="Database unavailable")


@app.get("/health")
def health():
    try:
        mc.ping()
    except PyMongoError:
        raise db_unavailable()
    return {"status": "ok", "service": "analytics-service", "mongodb": "connected"}


@app.post("/metrics", status_code=201)
def add_metric(metric: MetricIn):
    try:
        mc.record_metric(metric.name, metric.value, metric.tags)
    except PyMongoError:
        raise db_unavailable()
    return {"status": "recorded", "name": metric.name}


@app.get("/metrics/{name}/summary")
def metric_summary(name: str, hours: int = Query(24, ge=1, le=720)):
    try:
        return mc.metric_summary(name, hours)
    except PyMongoError:
        raise db_unavailable()


@app.post("/logs", status_code=201)
def add_log(entry: LogIn):
    try:
        mc.record_log(entry.level, entry.message, entry.service)
    except PyMongoError:
        raise db_unavailable()
    return {"status": "recorded", "level": entry.level}


@app.get("/logs")
def list_logs(level: str | None = None, limit: int = Query(20, ge=1, le=200)):
    try:
        return mc.recent_logs(level, limit)
    except PyMongoError:
        raise db_unavailable()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)