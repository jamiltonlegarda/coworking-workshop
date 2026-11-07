# Informe de Chaos Engineering

Proyecto: coworking-workshop (catalog-service + orders-service con FastAPI y Docker Compose)
Fecha de las pruebas: 2026-11-07

## Test 1: Caída de servicio dependiente

- **Escenario**: Se apagó `catalog-service` con `docker-compose stop catalog-service` mientras `orders-service` seguía corriendo. Se automatizó en `chaos-test.sh`.
- **Impacto**: `POST /orders` devolvió `503 Catalog service unavailable`. `orders-service` no se cayó: `GET /health` siguió respondiendo `200 ok`. Mientras el catálogo estuvo caído no se pudieron crear órdenes, porque el precio depende del catálogo (dependencia fuerte).
- **Tiempo de detección**: Inmediato. La primera petición hecha tras apagar el catálogo ya devolvió 503. `orders-service` captura el error de conexión con `httpx` (timeout de 3 s) y responde un error controlado.
- **Recuperación**: Automática y sin tocar `orders-service`. El catálogo arrancó en 0,2 s con `docker-compose start catalog-service` y la primera orden exitosa fue unos 5 s después (el script espera 5 s antes de probar). Volvió `200 OK`.

## Test 2: Cambio de contrato sin versionar

- **Escenario**: Se renombró en `catalog-service` el campo `price_per_hour` a `hourly_rate` (clase `Space` y datos) sin avisar ni versionar la API, y se reconstruyó solo el catálogo. `orders-service` no se modificó.
- **Impacto**: El catálogo siguió sano y respondiendo `200` con `hourly_rate`. `orders-service` también siguió sano (`/health` ok), pero `POST /orders` devolvió `500 Internal Server Error`. Ningún servicio estaba caído y la integración estaba rota. Observación adicional: en un primer intento mal aplicado (se cambió el campo en los datos pero no en la clase `Space`) el catálogo falló al arrancar con un `ValidationError` de Pydantic y `orders-service` respondió `503`, un síntoma idéntico a una caída del servicio y difícil de distinguir de ella.
- **Causa raíz**: `orders-service` depende de un contrato implícito. En `main.py` accede directamente a `space["price_per_hour"]` sin validar la respuesta, y el catálogo cambió el nombre del campo (un cambio incompatible) sin versionar ni avisar. El resultado fue un `KeyError: 'price_per_hour'` no controlado. No hay pruebas de contrato que detecten el cambio antes del despliegue.
- **Solución propuesta**:
  - Versionar la API (`/v1/spaces` mantiene `price_per_hour`; `/v2/spaces` introduce `hourly_rate`) y retirar la versión vieja de forma gradual.
  - Durante la transición, devolver ambos campos.
  - Agregar pruebas de contrato (por ejemplo con Pact o validación con Pydantic en el consumidor) en la integración continua para detectar cambios incompatibles antes de desplegar.
  - En `orders-service`, validar la respuesta del catálogo y responder un error claro (por ejemplo 502 "Contrato del catálogo inválido") en lugar de un 500 genérico.
  - Comunicar los cambios de API con un changelog y un periodo de deprecación.

## Conclusión

Los dos fallos se manifiestan distinto. Cuando el servicio cae, el error es evidente (503). Cuando cambia el contrato, todo parece sano pero la integración falla en silencio (500). Los sistemas distribuidos necesitan manejo de errores, versionado de contratos y pruebas de integración, no solo servicios que arranquen.