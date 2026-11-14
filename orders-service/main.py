import os

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from database import Order, get_db, run_with_retry

app = FastAPI(title="Orders Service")

# En Docker Compose, "catalog-service" es el nombre DNS del otro contenedor.
CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog-service:8001")


class SpaceChanged(BaseModel):
    space_id: int
    available: bool
    changed_at: str | None = None


# Espacios que el catalogo marco como no disponibles (en memoria)
UNAVAILABLE_SPACES = set()


def order_to_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "space_id": order.space_id,
        "space_name": order.space_name,
        "user_email": order.user_email,
        "hours": order.hours,
        "total_price": order.total_price,
        "created_at": order.created_at.isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "orders-service"}


@app.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    try:
        orders = run_with_retry(
            lambda: db.query(Order).order_by(Order.id).all(), db
        )
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return [order_to_dict(o) for o in orders]


@app.post("/events/space-changed")
def handle_space_changed(evt: SpaceChanged):
    if evt.available:
        UNAVAILABLE_SPACES.discard(evt.space_id)
    else:
        UNAVAILABLE_SPACES.add(evt.space_id)
    return {"status": "processed", "unavailable_spaces": sorted(UNAVAILABLE_SPACES)}


@app.post("/orders")
def create_order(
    space_id: int,
    user_email: str,
    hours: int,
    db: Session = Depends(get_db),
):
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be greater than 0")

    if space_id in UNAVAILABLE_SPACES:
        raise HTTPException(status_code=409, detail="Space not available")

    # 1. Consultar al catalog-service por HTTP
    try:
        response = httpx.get(f"{CATALOG_URL}/spaces/{space_id}", timeout=3.0)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Catalog service unavailable")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Space not found")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Unexpected response from catalog")

    # 2. Usar los datos del catalogo (aqui esta la dependencia del contrato)
    space = response.json()
    total_price = space["price_per_hour"] * hours

    # 3. Guardar la orden en PostgreSQL (con reintentos)
    def save_order():
        order = Order(
            space_id=space_id,
            space_name=space["name"],
            user_email=user_email,
            hours=hours,
            total_price=total_price,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    try:
        order = run_with_retry(save_order, db)
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return order_to_dict(order)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)