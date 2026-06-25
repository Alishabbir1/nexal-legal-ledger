"""Reproduce sunthessmunir SSO failure scenarios and print exact tracebacks."""
import os
import sys
import tempfile
import traceback
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PORTAL_FIRM_ID = "498205b5-0d17-453c-a0de-e507955e94fb"
FIRM_USER_ID = "2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc"
CUSTOMER_ID = "7a0a8a6e-dfc2-444e-9bd0-10e13af27035"
EMAIL = "sunthessmunir@gmail.com"
BCRYPT = "$2b$12$abcdefghijklmnopqrstuvwx.yz012345678901234567890"


def run_sso(label: str, setup_fn, portal_user_id: str, env_root: str):
    from db_router import reset_router

    reset_router()
    import firm_middleware
    import importlib
    importlib.reload(firm_middleware)
    from app import app
    from sso_auth import generate_sso_token

    os.environ["NEXAL_DATA_DIR"] = env_root
    setup_fn()
    token = generate_sso_token(
        user_id=portal_user_id,
        email=EMAIL,
        firm_id=PORTAL_FIRM_ID,
        role="firm_admin",
        username="sunthessmunir",
        extra={
            "firm_name": "new",
            "subscription_tier": "essential",
            "password_hash": BCRYPT,
            "portal_customer_id": CUSTOMER_ID,
        },
    )
    client = app.test_client()
    try:
        response = client.get("/auth/sso?token=" + token)
        print(f"\n=== {label} ===")
        print("STATUS", response.status_code)
        print("LOCATION", response.headers.get("Location"))
        print("BODY", response.get_data(as_text=True)[:500])
        if response.status_code == 302:
            follow = client.get("/client-ledger")
            print("CLIENT_LEDGER", follow.status_code, follow.headers.get("Location"))
    except Exception:
        print(f"\n=== {label} UNCAUGHT ===")
        traceback.print_exc()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SSO_SECRET_KEY"] = "repro-secret"

        from nexal_platform.platform_db import PlatformDatabase
        from nexal_platform.provision import provision_firm

        def setup_provisioned():
            provision_firm(
                name="new",
                slug="new-498205b5",
                portal_firm_id=PORTAL_FIRM_ID,
                owner_email=EMAIL,
                portal_user_id=FIRM_USER_ID,
                subscription_tier="essential",
            )

        def setup_stale_customer_id_user():
            result = provision_firm(
                name="new",
                slug="new-498205b5",
                portal_firm_id=PORTAL_FIRM_ID,
                owner_email=EMAIL,
                portal_user_id=CUSTOMER_ID,
                subscription_tier="essential",
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
                ("sunthessmunir", BCRYPT, CUSTOMER_ID, EMAIL.lower(), result["firm"]["id"]),
            )
            conn.commit()
            conn.close()

        def setup_email_conflict_two_users():
            result = provision_firm(
                name="new",
                slug="new-498205b5",
                portal_firm_id=PORTAL_FIRM_ID,
                owner_email="other@example.com",
                portal_user_id=str(uuid.uuid4()),
                subscription_tier="essential",
            )
            db = __import__("db_router").get_db_for_firm(result["firm"]["id"])
            conn = db.get_connection()
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id, temporary_password)
                VALUES ('sunthessmunir', ?, 'admin', 1, ?, ?, ?, 0)
                """,
                (BCRYPT, CUSTOMER_ID, EMAIL.lower(), result["firm"]["id"]),
            )
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id, temporary_password)
                VALUES ('staff1', ?, 'staff', 1, ?, 'staff1@example.com', ?, 0)
                """,
                (BCRYPT, str(uuid.uuid4()), result["firm"]["id"]),
            )
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id, temporary_password)
                VALUES ('staff2', ?, 'staff', 1, ?, 'staff2@example.com', ?, 0)
                """,
                (BCRYPT, str(uuid.uuid4()), result["firm"]["id"]),
            )
            conn.commit()
            conn.close()

        run_sso("fresh_provision", setup_provisioned, FIRM_USER_ID, os.path.join(tmp, "s1"))
        run_sso("stale_customer_portal_user_id", setup_stale_customer_id_user, FIRM_USER_ID, os.path.join(tmp, "s2"))
        run_sso("email_conflict_at_user_limit", setup_email_conflict_two_users, FIRM_USER_ID, os.path.join(tmp, "s3"))


if __name__ == "__main__":
    main()
