#!/bin/bash
# Chaos test: apagar catalog-service y observar el comportamiento de orders-service
# (Lab 10: ahora POST /orders exige JWT, asi que primero se hace login.)

BASE="http://localhost:8002"
EMAIL="chaos@test.com"
PASSWORD="ClaveChaos123"   # usuario de prueba, solo para este experimento

echo "=== 0. Autenticacion ($(date +%T)) ==="
# Registrar el usuario de prueba; si ya existe responde 409 y se ignora
curl -s -o /dev/null -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"

TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -d "username=$EMAIL&password=$PASSWORD" \
  | sed -E 's/.*"access_token":"([^"]+)".*/\1/')

if [ -z "$TOKEN" ] || [ "${#TOKEN}" -gt 600 ]; then
  echo "No se pudo obtener el token. Esta orders-service arriba?"
  exit 1
fi
echo "Token obtenido OK"

URL="$BASE/orders?space_id=1&hours=1"
AUTH="Authorization: Bearer $TOKEN"

echo ""
echo "=== 1. Estado normal ($(date +%T)) ==="
curl -s -w "\nHTTP %{http_code}\n" -X POST -H "$AUTH" "$URL"

echo ""
echo "=== 2. Apagando catalog-service ($(date +%T)) ==="
docker-compose stop catalog-service
sleep 2
echo "--- Intentando crear una orden con el catalogo caido ---"
curl -s -w "\nHTTP %{http_code}\n" -X POST -H "$AUTH" "$URL"
echo "--- orders-service sigue vivo? ---"
curl -s http://localhost:8002/health
echo ""

echo ""
echo "=== 3. Recuperando catalog-service ($(date +%T)) ==="
docker-compose start catalog-service
sleep 5
curl -s -w "\nHTTP %{http_code}\n" -X POST -H "$AUTH" "$URL"

echo ""
echo "=== Fin del chaos test ($(date +%T)) ==="