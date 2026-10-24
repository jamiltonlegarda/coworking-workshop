import os
from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Orders Service")

# En Docker Compose, "catalog-service" es el nombre DNS del otro contenedor.
CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog-service:8001")

ORDERS = []


@app.get("/health")
def health():
    return {"status": "ok", "service": "orders-service"}


@app.get("/orders")
def list_orders():
    return ORDERS


@app.post("/orders")
def create_order(space_id: int, user_email: str, hours: int):
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be greater than 0")

    # 1. Consultar al catalog-service por HTTP
    try:
        response = httpx.get(f"{CATALOG_URL}/spaces/{space_id}", timeout=3.0)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Catalog service unavailable")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Space not found")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Unexpected response from catalog")

    # 2. Usar los datos del catálogo (aquí está la dependencia del contrato)
    space = response.json()
    total_price = space["price_per_hour"] * hours

    # 3. Guardar la orden en memoria
    order = {
        "id": len(ORDERS) + 1,
        "space_id": space_id,
        "space_name": space["name"],
        "user_email": user_email,
        "hours": hours,
        "total_price": total_price,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ORDERS.append(order)
    return order


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)