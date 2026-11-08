import hashlib
import json
import jwt_stub

from app.database import find_user

SECRET_KEY = "super-secret-signing-key-123"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()


def login(username, password):
    user = find_user(username)
    if user is None:
        return None
    if user[2] == hash_password(password):
        return generate_token(user)
    return None


def generate_token(user):
    payload = json.dumps({"sub": user[0], "role": user[3]})
    return jwt_stub.encode(payload, SECRET_KEY)


def validate_token(token):
    try:
        return jwt_stub.decode(token, SECRET_KEY)
    except:
        pass


def load_config(path):
    return json.load(open(path))
