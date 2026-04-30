"""
CAFS – Community Appointment & Feedback System
Lambda handler: Appointments CRUD
AWS Services used: API Gateway → Lambda → DynamoDB
Author: Student Project
"""

import json
import boto3
import uuid
import os
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# ─── AWS resource references (injected via Lambda environment variables) ───
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
TABLE_NAME = os.environ.get("APPOINTMENTS_TABLE", "cafs-appointments")
table = dynamodb.Table(TABLE_NAME)

# ─── SNS topic for confirmation emails (optional – gracefully degrades) ───
sns = boto3.client("sns")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

# ─── CORS headers for API Gateway integration ─────────────────────────────
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Content-Type": "application/json",
}


def respond(status: int, body: dict) -> dict:
    """Return a formatted API Gateway proxy response."""
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


# ─── HANDLER ENTRY POINT ──────────────────────────────────────────────────
def lambda_handler(event: dict, context) -> dict:
    """
    Route HTTP method + resource path to the correct CRUD operation.
    API Gateway resource pattern: /appointments/{id}
    """
    http_method = event.get("httpMethod", "GET")
    path_params = event.get("pathParameters") or {}
    appointment_id = path_params.get("id")

    # Pre-flight CORS
    if http_method == "OPTIONS":
        return respond(200, {})

    try:
        if http_method == "GET" and not appointment_id:
            return get_all_appointments(event)
        elif http_method == "GET" and appointment_id:
            return get_appointment(appointment_id)
        elif http_method == "POST":
            return create_appointment(event)
        elif http_method == "PUT" and appointment_id:
            return update_appointment(appointment_id, event)
        elif http_method == "DELETE" and appointment_id:
            return delete_appointment(appointment_id)
        else:
            return respond(400, {"error": "Unsupported route"})

    except ClientError as e:
        # Surface DynamoDB errors without leaking stack traces
        code = e.response["Error"]["Code"]
        return respond(500, {"error": f"DynamoDB error: {code}"})
    except Exception as e:
        return respond(500, {"error": str(e)})


# ─── CREATE ───────────────────────────────────────────────────────────────
def create_appointment(event: dict) -> dict:
    """
    POST /appointments
    Body: { name, email, service, date, time, notes }
    Generates a UUID, persists to DynamoDB, optionally sends SNS notification.
    """
    body = json.loads(event.get("body", "{}"))
    required_fields = ["name", "email", "service", "date", "time"]

    # Validate required fields
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return respond(400, {"error": f"Missing fields: {', '.join(missing)}"})

    item = {
        "appointmentId": str(uuid.uuid4()),   # Partition key
        "name": body["name"],
        "email": body["email"],
        "service": body["service"],           # e.g. "GP Consultation", "Library Booking"
        "date": body["date"],                 # ISO format: YYYY-MM-DD
        "time": body["time"],                 # HH:MM
        "notes": body.get("notes", ""),
        "status": "PENDING",                  # PENDING | CONFIRMED | CANCELLED
        "createdAt": datetime.utcnow().isoformat(),
        "updatedAt": datetime.utcnow().isoformat(),
    }

    table.put_item(Item=item)

    # Fire-and-forget SNS notification (email/SMS confirmation)
    if SNS_TOPIC_ARN:
        try:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=f"New appointment: {item['name']} on {item['date']} at {item['time']} for {item['service']}.",
                Subject="CAFS – Appointment Confirmed",
            )
        except Exception:
            pass  # Non-fatal: notification failure should not block booking

    return respond(201, {"message": "Appointment created", "appointmentId": item["appointmentId"]})


# ─── READ ALL ─────────────────────────────────────────────────────────────
def get_all_appointments(event: dict) -> dict:
    """
    GET /appointments
    Optional query string: ?status=PENDING&service=GP+Consultation
    Uses DynamoDB Scan (acceptable for this demo scale; use GSI for production).
    """
    query_params = event.get("queryStringParameters") or {}
    status_filter = query_params.get("status")
    service_filter = query_params.get("service")

    # Full table scan – in production, replace with GSI-backed Query
    response = table.scan()
    items = response.get("Items", [])

    # Handle pagination (DynamoDB returns max 1 MB per call)
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    # Client-side filtering after scan
    if status_filter:
        items = [i for i in items if i.get("status") == status_filter.upper()]
    if service_filter:
        items = [i for i in items if i.get("service") == service_filter]

    # Sort by date then time
    items.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))

    return respond(200, {"appointments": items, "count": len(items)})


# ─── READ ONE ─────────────────────────────────────────────────────────────
def get_appointment(appointment_id: str) -> dict:
    """GET /appointments/{id}"""
    result = table.get_item(Key={"appointmentId": appointment_id})
    item = result.get("Item")
    if not item:
        return respond(404, {"error": "Appointment not found"})
    return respond(200, item)


# ─── UPDATE ───────────────────────────────────────────────────────────────
def update_appointment(appointment_id: str, event: dict) -> dict:
    """
    PUT /appointments/{id}
    Supports partial update of: status, notes, date, time
    Uses DynamoDB UpdateExpression to avoid overwriting unrelated fields.
    """
    body = json.loads(event.get("body", "{}"))
    allowed = ["status", "notes", "date", "time", "service"]
    updates = {k: v for k, v in body.items() if k in allowed}

    if not updates:
        return respond(400, {"error": "No updatable fields provided"})

    # Validate status transitions
    if "status" in updates and updates["status"] not in ("PENDING", "CONFIRMED", "CANCELLED"):
        return respond(400, {"error": "Invalid status value"})

    updates["updatedAt"] = datetime.utcnow().isoformat()

    # Build DynamoDB UpdateExpression dynamically
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
    expr_names = {f"#{k}": k for k in updates}
    expr_values = {f":{k}": v for k, v in updates.items()}

    table.update_item(
        Key={"appointmentId": appointment_id},
        UpdateExpression=expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
        ConditionExpression="attribute_exists(appointmentId)",  # 404 guard
    )

    return respond(200, {"message": "Appointment updated", "appointmentId": appointment_id})


# ─── DELETE ───────────────────────────────────────────────────────────────
def delete_appointment(appointment_id: str) -> dict:
    """DELETE /appointments/{id} – soft-delete by setting status=CANCELLED."""
    # Prefer soft delete to preserve audit trail in DynamoDB
    table.update_item(
        Key={"appointmentId": appointment_id},
        UpdateExpression="SET #s = :s, #u = :u",
        ExpressionAttributeNames={"#s": "status", "#u": "updatedAt"},
        ExpressionAttributeValues={
            ":s": "CANCELLED",
            ":u": datetime.utcnow().isoformat(),
        },
        ConditionExpression="attribute_exists(appointmentId)",
    )
    return respond(200, {"message": "Appointment cancelled"})
