import enum
import os
import time
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

# La cadena de conexion viene del entorno (docker-compose / .env), nunca del codigo.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no esta definida")

# Pool de conexiones:
# - pool_size / max_overflow: conexiones permanentes y extra bajo carga
# - pool_pre_ping: prueba la conexion antes de usarla (evita conexiones muertas)
# - pool_recycle: renueva conexiones viejas cada 30 minutos
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    space_id = Column(Integer, nullable=False, index=True)
    space_name = Column(String(100), nullable=False)
    user_email = Column(String(255), nullable=False, index=True)
    hours = Column(Integer, nullable=False)
    total_price = Column(Float, nullable=False)
    status = Column(SQLEnum(OrderStatus), nullable=False, default=OrderStatus.CONFIRMED)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="user")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def get_db():
    """Dependencia de FastAPI: abre una sesion por peticion y la cierra al terminar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_with_retry(operation, db, retries=3, delay=0.5):
    """Ejecuta operation(); si la base falla, hace rollback, espera y reintenta.

    Espera delay, 2*delay, ... entre intentos. Tras el ultimo intento
    fallido vuelve a lanzar OperationalError para que el endpoint responda 503.
    """
    for attempt in range(1, retries + 1):
        try:
            return operation()
        except OperationalError:
            db.rollback()
            if attempt == retries:
                raise
            time.sleep(delay * attempt)