import os

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from auth import (
    ROLE_LEVELS,
    RegisterRequest,
    Token,
    UserInDB,
    authenticate_user,
    create_access_token,
    get_current_user,
    get_password_hash,
    get_user_by_email,
    require_role,
)
from database import Order, User, get_db, run_with_retry

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


# ----------------------------- Autenticacion -----------------------------
@app.post("/auth/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Registro publico. Siempre crea rol 'user': nadie se asigna admin solo."""
    if get_user_by_email(db, payload.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    def save_user():
        user = User(
            email=payload.email,
            hashed_password=get_password_hash(payload.password),
            role="user",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    try:
        user = run_with_retry(save_user, db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"id": user.id, "email": user.email, "role": user.role}


@app.post("/auth/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    # OAuth2 usa el campo "username"; aqui el username es el correo.
    user = authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": user.email, "role": user.role})
    return Token(access_token=token, token_type="bearer")


@app.get("/auth/me")
def me(current_user: UserInDB = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email, "role": current_user.role}


# ------------------------------ Administracion ---------------------------
@app.get("/admin/users")
def list_users(
    _: UserInDB = Depends(require_role("admin")), db: Session = Depends(get_db)
):
    try:
        users = run_with_retry(lambda: db.query(User).order_by(User.id).all(), db)
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return [{"id": u.id, "email": u.email, "role": u.role} for u in users]


@app.put("/admin/users/{email}/role")
def change_role(
    email: str,
    role: str,
    _: UserInDB = Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    if role not in ROLE_LEVELS:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(ROLE_LEVELS)}")
    user = get_user_by_email(db, email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    def update():
        user.role = role
        db.commit()

    try:
        run_with_retry(update, db)
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"email": email, "role": role}


# --------------------------------- Ordenes -------------------------------
@app.get("/orders")
def list_orders(
    current_user: UserInDB = Depends(get_current_user), db: Session = Depends(get_db)
):
    """user ve solo sus ordenes; admin y superadmin ven todas."""

    def query():
        q = db.query(Order).order_by(Order.id)
        if ROLE_LEVELS.get(current_user.role, 0) < ROLE_LEVELS["admin"]:
            q = q.filter(Order.user_email == current_user.email)
        return q.all()

    try:
        orders = run_with_retry(query, db)
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return [order_to_dict(o) for o in orders]


@app.post("/events/space-changed")
def handle_space_changed(evt: SpaceChanged):
    # Sin JWT a proposito: lo llama el consumer (SQS/Lambda), no una persona.
    if evt.available:
        UNAVAILABLE_SPACES.discard(evt.space_id)
    else:
        UNAVAILABLE_SPACES.add(evt.space_id)
    return {"status": "processed", "unavailable_spaces": sorted(UNAVAILABLE_SPACES)}


@app.post("/orders")
def create_order(
    space_id: int,
    hours: int,
    current_user: UserInDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # El correo sale del token: ya no lo manda el cliente.
    user_email = current_user.email

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