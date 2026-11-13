import json
import os
from datetime import datetime, timezone

import boto3

events = boto3.client("events")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

# En memoria: no persiste entre instancias de Lambda (en un caso real iria DynamoDB)
SPACES = {
    1: {"id": 1, "name": "Sala Creativa", "capacity": 6, "price_per_hour": 25.0, "available": True},
    2: {"id": 2, "name": "Escritorio Compartido", "capacity": 1, "price_per_hour": 8.0, "available": True},
    3: {"id": 3, "name": "Sala de Juntas", "capacity": 12, "price_per_hour": 40.0, "available": True},
}


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def lambda_handler(event, context):
    method = event.get("httpMethod")
    resource = event.get("resource")

    if method == "GET" and resource == "/catalog":
        return _response(200, list(SPACES.values()))

    raw_id = (event.get("pathParameters") or {}).get("space_id", "")
    if not raw_id.isdigit() or int(raw_id) not in SPACES:
        return _response(404, {"detail": "Space not found"})
    space = SPACES[int(raw_id)]

    if method == "GET" and resource == "/catalog/{space_id}":
        return _response(200, space)

    if method == "PUT" and resource == "/catalog/{space_id}/availability":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"detail": "Invalid JSON"})
        if not isinstance(body.get("available"), bool):
            return _response(400, {"detail": "'available' (boolean) is required"})

        space["available"] = body["available"]
        detail = {
            "space_id": space["id"],
            "available": space["available"],
            "changed_at": datetime.now(timezone.utc).isoformat(),
        }
        result = events.put_events(Entries=[{
            "Source": "coworking.catalog",
            "DetailType": "SpaceChanged",
            "Detail": json.dumps(detail),
            "EventBusName": EVENT_BUS_NAME,
        }])
        if result["FailedEntryCount"] > 0:
            return _response(502, {"detail": "Event could not be published"})
        return _response(200, {"space": space, "event_published": "SpaceChanged"})

    return _response(405, {"detail": "Method not allowed"})