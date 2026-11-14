"""Lab 10: autenticacion JWT + RBAC para orders-service."""
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from database import User, get_db, run_with_retry

# --- Configuracion de seguridad (todo viene del entorno, nunca del codigo) ---
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY no esta definida")
ALGORITHM = "HS256"
# Un solo valor para todo: el token dura 60 minutos por defecto.
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# --- Roles: a mayor nivel, mas permisos ---
ROLE_LEVELS = {"user": 1, "admin": 2, "superadmin": 3}


# --- Modelos ---
class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: str | None = None
    role: str | None = None


class UserInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    hashed_password: str
    role: str


class RegisterRequest(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    # bcrypt solo usa los primeros 72 bytes
    password: str = Field(min_length=8, max_length=72)


# --- Contrasenas ---
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# --- Token JWT ---
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# --- Acceso a la base de datos ---
def get_user_by_email(db: Session, email: str) -> User | None:
    try:
        return run_with_retry(
            lambda: db.query(User).filter(User.email == email).first(), db
        )
    except OperationalError:
        raise HTTPException(status_code=503, detail="Database unavailable")


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user


# --- Dependencias de FastAPI ---
def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> UserInDB:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # decode tambien valida la firma y la expiracion (exp)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email, role=payload.get("role"))
    except JWTError:
        raise credentials_exception

    user = get_user_by_email(db, token_data.email)
    if user is None:  # el usuario pudo ser borrado despues de emitir el token
        raise credentials_exception
    # El rol que vale es el de la base de datos, no el que traia el token.
    return UserInDB.model_validate(user)


def require_role(min_role: str):
    """Permite el acceso solo si el rol del usuario tiene al menos el nivel min_role."""
    min_level = ROLE_LEVELS[min_role]

    def checker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
        if ROLE_LEVELS.get(current_user.role, 0) < min_level:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user

    return checker