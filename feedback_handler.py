"""
CAFS – Community Appointment & Feedback System
Lambda handler: Feedback CRUD
AWS Services: API Gateway → Lambda → DynamoDB + S3 (attachment upload URL)
"""

import json
import boto3
import uuid
import os
from datetime import datetime
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
s3 = boto3.client("s3")

TABLE_NAME = os.environ.get("FEEDBACK_TABLE", "cafs-feedback")
BUCKET_NAME = os.environ.get("FEEDBACK_BUCKET", "cafs-feedback-attachments")
table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
    "Content-Type": "application/json",
}

VALID_CATEGORIES = {"GENERAL", "FACILITY", "STAFF", "APPOINTMENT", "SUGGESTION"}
VALID_RATINGS = {1, 2, 3, 4, 5}


def respond(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def lambda_handler(event: dict, context) -> dict:
    http_method = event.get("httpMethod", "GET")
    path_params = event.get("pathParameters") or {}
    resource = event.get("resource", "")
    feedback_id = path_params.get("id")

    if http_method == "OPTIONS":
        return respond(200, {})

    try:
        if http_method == "GET" and not feedback_id:
            return get_all_feedback(event)
        elif http_method == "GET" and feedback_id:
            return get_feedback(feedback_id)
        elif http_method == "POST" and "/upload-url" in resource:
            return get_upload_url(event)
        elif http_method == "POST":
            return submit_feedback(event)
        elif http_method == "DELETE" and feedback_id:
            return delete_feedback(feedback_id)
        else:
            return respond(400, {"error": "Unsupported route"})
    except ClientError as e:
        return respond(500, {"error": e.response["Error"]["Code"]})
    except Exception as e:
        return respond(500, {"error": str(e)})


def submit_feedback(event: dict) -> dict:
    """
    POST /feedback
    Body: { category, rating, message, anonymous?, appointmentId? }
    Stores feedback in DynamoDB; attachments go directly to S3 via pre-signed URL.
    """
    body = json.loads(event.get("body", "{}"))

    # Validate inputs
    category = body.get("category", "GENERAL").upper()
    if category not in VALID_CATEGORIES:
        return respond(400, {"error": f"category must be one of {VALID_CATEGORIES}"})

    rating = body.get("rating")
    if rating is not None and int(rating) not in VALID_RATINGS:
        return respond(400, {"error": "rating must be 1-5"})

    message = body.get("message", "").strip()
    if not message:
        return respond(400, {"error": "message is required"})

    # Anonymise if requested – store no PII
    is_anonymous = bool(body.get("anonymous", False))
    submitter = "Anonymous" if is_anonymous else body.get("submitterName", "Unknown")
    email = "" if is_anonymous else body.get("email", "")

    item = {
        "feedbackId": str(uuid.uuid4()),
        "category": category,
        "rating": int(rating) if rating else None,
        "message": message,
        "submitter": submitter,
        "email": email,
        "anonymous": is_anonymous,
        "appointmentId": body.get("appointmentId", ""),   # Link to appointment if relevant
        "attachmentKey": body.get("attachmentKey", ""),   # S3 key after upload
        "status": "OPEN",                                  # OPEN | REVIEWED | RESOLVED
        "createdAt": datetime.utcnow().isoformat(),
    }

    table.put_item(Item=item)
    return respond(201, {"message": "Feedback submitted", "feedbackId": item["feedbackId"]})


def get_all_feedback(event: dict) -> dict:
    """
    GET /feedback?category=FACILITY&status=OPEN
    Returns paginated list of feedback entries.
    """
    query_params = event.get("queryStringParameters") or {}
    category_filter = query_params.get("category")
    status_filter = query_params.get("status")

    response = table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    if category_filter:
        items = [i for i in items if i.get("category") == category_filter.upper()]
    if status_filter:
        items = [i for i in items if i.get("status") == status_filter.upper()]

    # Sort by newest first
    items.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

    # Compute average rating for summary
    ratings = [int(i["rating"]) for i in items if i.get("rating")]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

    return respond(200, {
        "feedback": items,
        "count": len(items),
        "averageRating": avg_rating,
    })


def get_feedback(feedback_id: str) -> dict:
    """GET /feedback/{id}"""
    result = table.get_item(Key={"feedbackId": feedback_id})
    item = result.get("Item")
    if not item:
        return respond(404, {"error": "Feedback not found"})
    return respond(200, item)


def get_upload_url(event: dict) -> dict:
    """
    POST /feedback/upload-url
    Body: { filename, contentType }
    Returns a pre-signed S3 URL valid for 5 minutes so the browser can
    upload an attachment directly without routing through Lambda.
    This avoids the 6 MB API Gateway payload limit.
    """
    body = json.loads(event.get("body", "{}"))
    filename = body.get("filename", "attachment")
    content_type = body.get("contentType", "application/octet-stream")

    # Sanitise filename – no path traversal
    safe_name = os.path.basename(filename)
    key = f"feedback-attachments/{uuid.uuid4()}/{safe_name}"

    presigned_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
            # SSE with AWS managed keys – data encrypted at rest
            "ServerSideEncryption": "AES256",
        },
        ExpiresIn=300,  # 5 minutes
    )

    return respond(200, {"uploadUrl": presigned_url, "s3Key": key})


def delete_feedback(feedback_id: str) -> dict:
    """DELETE /feedback/{id} – admin soft-delete (sets status=DELETED)."""
    table.update_item(
        Key={"feedbackId": feedback_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "DELETED"},
        ConditionExpression="attribute_exists(feedbackId)",
    )
    return respond(200, {"message": "Feedback removed"})
