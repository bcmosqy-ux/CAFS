"""
CAFS jwt_authorizer.py - Lambda Authorizer for API Gateway
Verifies Cognito JWT tokens and returns IAM Allow/Deny policy.
"""
import json, os, base64, time

REGION       = os.environ.get("AWS_REGION","eu-west-1")
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID","")
CLIENT_ID    = os.environ.get("COGNITO_CLIENT_ID","")

def lambda_handler(event, context):
    token      = event.get("authorizationToken","")
    method_arn = event.get("methodArn","")
    if not token.startswith("Bearer "):
        raise Exception("Unauthorized")
    jwt = token.replace("Bearer ","").strip()
    try:
        claims = verify_jwt(jwt)
    except Exception as e:
        print(f"Token verification failed: {e}")
        raise Exception("Unauthorized")
    user_sub = claims.get("sub","")
    email    = claims.get("email","")
    groups   = claims.get("cognito:groups",[])
    role     = "ADMIN" if "Admins" in groups else "USER"
    return build_policy(user_sub,"Allow",method_arn,{"userSub":user_sub,"email":email,"role":role})

def build_policy(principal_id, effect, method_arn, context=None):
    parts      = method_arn.split(":")
    region     = parts[3]
    account    = parts[4]
    api_parts  = parts[5].split("/")
    api_id     = api_parts[0]
    stage      = api_parts[1]
    resource   = f"arn:aws:execute-api:{region}:{account}:{api_id}/{stage}/*/*"
    policy = {"principalId":principal_id,"policyDocument":{"Version":"2012-10-17",
        "Statement":[{"Action":"execute-api:Invoke","Effect":effect,"Resource":resource}]}}
    if context:
        policy["context"] = context
    return policy

def verify_jwt(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    payload = json.loads(_b64_decode(parts[1]))
    if payload.get("exp",0) < time.time():
        raise ValueError("Token has expired")
    if payload.get("aud") != CLIENT_ID and payload.get("client_id") != CLIENT_ID:
        raise ValueError("Token audience mismatch")
    expected_iss = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
    if payload.get("iss") != expected_iss:
        raise ValueError("Token issuer mismatch")
    if payload.get("token_use","") not in ("id","access"):
        raise ValueError("Invalid token_use")
    return payload

def _b64_decode(data):
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)
