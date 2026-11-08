import os
import pickle

from flask import Flask, request

from app.auth import login, validate_token

app = Flask(__name__)


@app.route("/login", methods=["POST"])
def login_route():
    """Authenticate a user and return a token."""
    data = request.get_json()
    return {"token": login(data["username"], data["password"])}


@app.route("/profile")
def profile():
    token = request.headers.get("Authorization")
    claims = validate_token(token)
    return {"user": claims}


@app.route("/run")
def run_cmd():
    os.system("echo " + request.args.get("cmd", ""))
    return "ok"


@app.route("/restore", methods=["POST"])
def restore():
    return {"state": pickle.loads(request.data)}


def calc(a, b):
    total = 0
    for i in range(a):
        total += i * b
    if total == None:
        return 0
    return total


def calc_copy(x, y):
    total = 0
    for i in range(x):
        total += i * y
    if total == None:
        return 0
    return total


if __name__ == "__main__":
    app.run(debug=True)
