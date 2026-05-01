"""
CAFS auth_handler.py - Cognito authentication Lambda
Handles: register, login, logout, refresh, get current user
"""
import json, boto3, os, hmac, hashlib, base64
from botocore.exceptions import ClientError

cognito = boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION","eu-west-1"))
USER_POOL_ID  = os.environ.get("COGNITO_USER_POOL_ID","")
CLIENT_ID     = os.environ.get("COGNITO_CLIENT_ID","")
CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET","")

CORS = {"Access-Control-Allow-Origin":"*","Access-Control-Allow-Headers":"Content-Type,Authorization","Access-Control-Allow-Methods":"GET,POST,DELETE,OPTIONS","Content-Type":"application/json"}

def respond(status, body):
    return {"statusCode":status,"headers":CORS,"body":json.dumps(body)}

def secret_hash(username):
    msg = username + CLIENT_ID
    dig = hmac.new(CLIENT_SECRET.encode("utf-8"), msg.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(dig).decode()

def lambda_handler(event, context):
    method   = event.get("httpMethod","GET")
    resource = event.get("resource","")
    if method == "OPTIONS": return respond(200,{})
    try:
        if "/auth/register" in resource and method=="POST": return register(event)
        elif "/auth/login"  in resource and method=="POST": return login(event)
        elif "/auth/logout" in resource and method=="POST": return logout(event)
        elif "/auth/me"     in resource and method=="GET":  return get_me(event)
        else: return respond(404,{"error":"Route not found"})
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        http = {"UsernameExistsException":409,"NotAuthorizedException":401,"UserNotFoundException":404,"UserNotConfirmedException":403,"InvalidPasswordException":400}.get(code,500)
        return respond(http,{"error":msg,"code":code})
    except Exception as e:
        return respond(500,{"error":str(e)})

def register(event):
    body     = json.loads(event.get("body","{}"))
    email    = body.get("email","").strip().lower()
    password = body.get("password","").strip()
    name     = body.get("name","").strip()
    role     = body.get("role","USER").upper()
    if not email or not password or not name:
        return respond(400,{"error":"email, password, and name are required"})
    if len(password) < 8:
        return respond(400,{"error":"Password must be at least 8 characters"})
    if role == "ADMIN":
        invite = body.get("inviteCode","")
        if invite != os.environ.get("ADMIN_INVITE_CODE","CAFS-ADMIN-2026"):
            return respond(403,{"error":"Invalid admin invite code"})
    cognito.sign_up(ClientId=CLIENT_ID, SecretHash=secret_hash(email), Username=email, Password=password,
        UserAttributes=[{"Name":"email","Value":email},{"Name":"name","Value":name},{"Name":"custom:role","Value":role}])
    cognito.admin_confirm_sign_up(UserPoolId=USER_POOL_ID, Username=email)
    group = "Admins" if role=="ADMIN" else "Users"
    cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=email, GroupName=group)
    return respond(201,{"message":f"Account created. Welcome, {name}!","email":email,"role":role})

def login(event):
    body     = json.loads(event.get("body","{}"))
    email    = body.get("email","").strip().lower()
    password = body.get("password","").strip()
    if not email or not password:
        return respond(400,{"error":"email and password are required"})
    result = cognito.initiate_auth(AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME":email,"PASSWORD":password,"SECRET_HASH":secret_hash(email)},
        ClientId=CLIENT_ID)
    auth = result["AuthenticationResult"]
    user_data = cognito.get_user(AccessToken=auth["AccessToken"])
    attrs = {a["Name"]:a["Value"] for a in user_data["UserAttributes"]}
    groups = cognito.admin_list_groups_for_user(Username=email, UserPoolId=USER_POOL_ID)
    role = "ADMIN" if any(g["GroupName"]=="Admins" for g in groups["Groups"]) else "USER"
    return respond(200,{"idToken":auth["IdToken"],"accessToken":auth["AccessToken"],
        "refreshToken":auth["RefreshToken"],"expiresIn":auth["ExpiresIn"],
        "user":{"email":attrs.get("email",email),"name":attrs.get("name",""),"role":role,"sub":attrs.get("sub","")}})

def logout(event):
    auth_header = event.get("headers",{}).get("Authorization","")
    if not auth_header.startswith("Bearer "):
        return respond(401,{"error":"Missing or invalid Authorization header"})
    cognito.global_sign_out(AccessToken=auth_header.replace("Bearer ","").strip())
    return respond(200,{"message":"Logged out successfully"})

def get_me(event):
    auth_header = event.get("headers",{}).get("Authorization","")
    if not auth_header.startswith("Bearer "):
        return respond(401,{"error":"Missing or invalid Authorization header"})
    user_data = cognito.get_user(AccessToken=auth_header.replace("Bearer ","").strip())
    attrs = {a["Name"]:a["Value"] for a in user_data["UserAttributes"]}
    email = attrs.get("email","")
    groups = cognito.admin_list_groups_for_user(Username=email, UserPoolId=USER_POOL_ID)
    role = "ADMIN" if any(g["GroupName"]=="Admins" for g in groups["Groups"]) else "USER"
    return respond(200,{"email":email,"name":attrs.get("name",""),"role":role,"sub":attrs.get("sub","")})
