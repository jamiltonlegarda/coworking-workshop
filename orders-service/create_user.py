"""Crea (o actualiza) un usuario con un rol concreto, p. ej. el primer admin.

Uso dentro del contenedor:
  docker-compose exec orders-service python create_user.py correo@x.com Clave12345 admin
"""
import sys

from auth import ROLE_LEVELS, get_password_hash
from database import SessionLocal, User

if len(sys.argv) != 4 or sys.argv[3] not in ROLE_LEVELS:
    sys.exit(f"Uso: python create_user.py <email> <password> <{'|'.join(ROLE_LEVELS)}>")

email, password, role = sys.argv[1:4]
db = SessionLocal()
try:
    user = db.query(User).filter(User.email == email).first()
    if user:
        user.hashed_password = get_password_hash(password)
        user.role = role
        action = "actualizado"
    else:
        db.add(User(email=email, hashed_password=get_password_hash(password), role=role))
        action = "creado"
    db.commit()
    print(f"Usuario {email} {action} con rol {role}")
finally:
    db.close()