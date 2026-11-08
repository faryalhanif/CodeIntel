from app.auth import hash_password


def test_hash_password_is_deterministic():
    assert hash_password("a") == hash_password("a")
