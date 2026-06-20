"""Password verification supports Portal bcrypt and Ledger werkzeug hashes."""
import bcrypt
from werkzeug.security import generate_password_hash, check_password_hash

from lib.password_verification import verify_password


def test_verify_password_accepts_werkzeug_scrypt():
    password = "LedgerPass99"
    stored = generate_password_hash(password, method="scrypt")
    assert verify_password(stored, password)
    assert not verify_password(stored, "wrong")


def test_verify_password_accepts_portal_bcrypt():
    password = "MyPassword123"
    stored = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    assert stored.startswith("$2b$")
    try:
        werkzeug_ok = check_password_hash(stored, password)
    except ValueError:
        werkzeug_ok = False
    assert not werkzeug_ok
    assert verify_password(stored, password)
    assert not verify_password(stored, "MyPassword124")
