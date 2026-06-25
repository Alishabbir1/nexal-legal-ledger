"""Reproduce SSO for portal firm 498205b5 (sunthessmunir@gmail.com)."""
import os
import tempfile
import uuid

from app import app
from sso_auth import generate_sso_token

PORTAL_FIRM_ID = "498205b5-0d17-453c-a0de-e507955e94fb"
PORTAL_USER_ID = "2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc"
EMAIL = "sunthessmunir@gmail.com"

with tempfile.TemporaryDirectory() as tmp:
    os.environ["NEXAL_DATA_DIR"] = os.path.join(tmp, "nexal-sso-repro")
    os.environ["SSO_SECRET_KEY"] = "repro-secret-key"

    token = generate_sso_token(
        user_id=PORTAL_USER_ID,
        email=EMAIL,
        firm_id=PORTAL_FIRM_ID,
        role="firm_admin",
        username="sunthessmunir",
        extra={"firm_name": "new", "password_hash": "$2a$12$abcdefghijklmnopqrstuv"},
    )

    client = app.test_client()
    response = client.get("/auth/sso?token=" + token)
    print("STATUS", response.status_code)
    print("LOCATION", response.headers.get("Location"))
    print("BODY", response.get_data(as_text=True)[:500])
