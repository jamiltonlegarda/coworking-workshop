#!/bin/bash
# Chaos test: apagar catalog-service y observar el comportamiento de orders-service

URL="http://localhost:8002/orders?space_id=1&user_email=chaos@test.com&hours=1"

echo "=== 1. Estado normal ($(date +%T)) ==="
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL"

echo ""
echo "=== 2. Apagando catalog-service ($(date +%T)) ==="
docker-compose stop catalog-service
sleep 2
echo "--- Intentando crear una orden con el catalogo caido ---"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL"
echo "--- orders-service sigue vivo? ---"
curl -s http://localhost:8002/health
echo ""

echo ""
echo "=== 3. Recuperando catalog-service ($(date +%T)) ==="
docker-compose start catalog-service
sleep 5
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL"

echo ""
echo "=== Fin del chaos test ($(date +%T)) ==="