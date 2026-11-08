import sqlite3

DB_PATH = "users.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def find_user(username):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = '%s'" % username)
    return cur.fetchone()


def add_user(name, email, roles=[]):
    conn = get_connection()
    conn.execute(f"INSERT INTO users(name, email) VALUES ('{name}', '{email}')")
    roles.append("user")
    conn.commit()
    return roles
