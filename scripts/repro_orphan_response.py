"""Test orphan/corrupt tenant SSO response codes on current code."""
import os
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PORTAL_FIRM_ID = "498205b5-0d17-453c-a0de-e507955e94fb"
FIRM_USER_ID = "2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc"
EMAIL = "sunthessmunir@gmail.com"

with tempfile.TemporaryDirectory() as tmp:
    os.environ["NEXAL_DATA_DIR"] = os.path.join(tmp, "nexal-orphan")
    os.environ["SSO_SECRET_KEY"] = "repro-secret"

    from db_router import reset_router
    from nexal_platform.platform_db import PlatformDatabase
    from nexal_platform.portal_link import slug_from_portal_firm
    from sso_auth import generate_sso_token
    from app import app

    reset_router()
    platform = PlatformDatabase()
    firm = platform.create_firm(
        name="new",
        slug=slug_from_portal_firm("new", PORTAL_FIRM_ID),
        portal_firm_id=PORTAL_FIRM_ID,
    )
    db_path = platform.paths.tenant_db_path(firm["id"])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with open(db_path, "wb") as handle:
        handle.write(b"CORRUPT")
    platform.create_workspace(firm_id=firm["id"], database_path=db_path)

    token = generate_sso_token(
        user_id=FIRM_USER_ID,
        email=EMAIL,
        firm_id=PORTAL_FIRM_ID,
        role="firm_admin",
        username="sunthessmunir",
        extra={"firm_name": "new", "subscription_tier": "essential"},
    )
    client = app.test_client()
    response = client.get("/auth/sso?token=" + token)
    print("orphan_corrupt", response.status_code, response.get_data(as_text=True)[:300])

    # Missing workspace entirely
    reset_router()
    os.environ["NEXAL_DATA_DIR"] = os.path.join(tmp, "nexal-nows")
    platform2 = PlatformDatabase()
    platform2.create_firm(
        name="new",
        slug=slug_from_portal_firm("new", PORTAL_FIRM_ID),
        portal_firm_id=PORTAL_FIRM_ID,
    )
    response2 = app.test_client().get("/auth/sso?token=" + token)
    print("missing_workspace", response2.status_code, response2.get_data(as_text=True)[:300])
