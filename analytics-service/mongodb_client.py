import os
import time
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import ConnectionFailure

# La URI viene del entorno (.env / docker-compose), nunca del codigo.
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB") or "coworking_analytics"
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI no esta definida")

# Pool de conexiones y tiempos maximos de espera
client = MongoClient(
    MONGODB_URI,
    maxPoolSize=20,
    minPoolSize=1,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    retryWrites=True,
)
db = client[MONGODB_DB]
metrics = db["metrics"]
logs = db["logs"]

LOG_RETENTION_DAYS = 30


def utcnow():
    return datetime.now(timezone.utc)


def ensure_indexes():
    """Crea los indices (es seguro llamarlo varias veces)."""
    # Consultas por metrica en un rango de fechas
    metrics.create_index([("name", ASCENDING), ("timestamp", DESCENDING)])
    # Logs recientes, filtrados por nivel
    logs.create_index([("level", ASCENDING), ("timestamp", DESCENDING)])
    # TTL: Mongo borra solos los logs de mas de LOG_RETENTION_DAYS dias
    logs.create_index(
        [("timestamp", ASCENDING)],
        expireAfterSeconds=LOG_RETENTION_DAYS * 24 * 3600,
    )


def run_with_retry(operation, retries=3, delay=0.5):
    """Ejecuta operation(); ante fallos de conexion reintenta con espera creciente."""
    for attempt in range(1, retries + 1):
        try:
            return operation()
        except ConnectionFailure:
            if attempt == retries:
                raise
            time.sleep(delay * attempt)


def ping():
    return run_with_retry(lambda: client.admin.command("ping"))


def record_metric(name, value, tags=None):
    doc = {"name": name, "value": value, "tags": tags or {}, "timestamp": utcnow()}
    run_with_retry(lambda: metrics.insert_one(doc))
    return doc


def record_log(level, message, service="orders-service"):
    doc = {"level": level, "message": message, "service": service, "timestamp": utcnow()}
    run_with_retry(lambda: logs.insert_one(doc))
    return doc


def metric_summary(name, hours=24):
    """Cantidad, promedio, minimo y maximo de una metrica en las ultimas N horas."""
    since = utcnow() - timedelta(hours=hours)
    pipeline = [
        {"$match": {"name": name, "timestamp": {"$gte": since}}},
        {
            "$group": {
                "_id": "$name",
                "count": {"$sum": 1},
                "avg": {"$avg": "$value"},
                "min": {"$min": "$value"},
                "max": {"$max": "$value"},
            }
        },
    ]
    result = run_with_retry(lambda: list(metrics.aggregate(pipeline)))
    if not result:
        return {"name": name, "hours": hours, "count": 0}
    row = result[0]
    return {
        "name": name,
        "hours": hours,
        "count": row["count"],
        "avg": row["avg"],
        "min": row["min"],
        "max": row["max"],
    }


def recent_logs(level=None, limit=20):
    query = {"level": level} if level else {}
    cursor = logs.find(query, {"_id": 0}).sort("timestamp", DESCENDING).limit(limit)
    return run_with_retry(lambda: list(cursor))