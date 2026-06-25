"""Verify duplicate provision after email conflict returns controlled error."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

CUSTOMER_ID = "7a0a8a6e-dfc2-444e-9bd0-10e13af27035"
FIRM_USER_ID = "2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc"
EMAIL = "sunthessmunir@gmail.com"
PORTAL_FIRM_ID = "498205b5-0d17-453c-a0de-e507955e94fb"

with tempfile.TemporaryDirectory() as tmp:
    os.environ["NEXAL_DATA_DIR"] = os.path.join(tmp, "d")
    os.environ["SSO_SECRET_KEY"] = "s"
    from db_router import reset_router

    reset_router()
    from nexal_platform.provision import provision_firm
    from sso_auth import generate_sso_token
    from app import app

    result = provision_firm(
        name="new",
        slug="new-498205b5",
        portal_firm_id=PORTAL_FIRM_ID,
        owner_email=EMAIL,
        portal_user_id=CUSTOMER_ID,
    )
    db = __import__("db_router").get_db_for_firm(result["firm"]["id"])
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO users (
            username, password_hash, role, active,
            portal_user_id, email, firm_id, temporary_password
        ) VALUES (?, ?, 'admin', 1, ?, ?, ?, 0)
        """,
        ("sunthessmunir", "hash", CUSTOMER_ID, EMAIL, result["firm"]["id"]),
    )
    conn.commit()
    conn.close()

    token = generate_sso_token(
        FIRM_USER_ID,
        EMAIL,
        PORTAL_FIRM_ID,
        "firm_admin",
        username="sunthessmunir",
        extra={"firm_name": "new"},
    )
    response = app.test_client().get("/auth/sso?token=" + token)
    print("STATUS", response.status_code)
    print("BODY", response.get_data(as_text=True))
