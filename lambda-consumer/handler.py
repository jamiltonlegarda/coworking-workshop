import json
import os
import urllib.request
from datetime import datetime, timezone

ORDERS_URL = os.environ.get("ORDERS_URL", "")


def forward_to_orders(detail):
    req = urllib.request.Request(
        f"{ORDERS_URL}/events/space-changed",
        data=json.dumps(detail).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5).read()


def lambda_handler(event, context):
    processed = 0
    for record in event.get("Records", []):
        message = json.loads(record["body"])  # el evento completo de EventBridge
        detail = message.get("detail", {})
        print(json.dumps({
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "detail_type": message.get("detail-type"),
            "detail": detail,
        }))
        if ORDERS_URL:
            forward_to_orders(detail)  # si falla, el mensaje vuelve a la cola
        processed += 1
    return {"processed": processed}