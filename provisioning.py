"""
Nexal Legal – Phase 4A: Firm Provisioning
==========================================
provision_firm() is the single entry-point for creating a new firm
in the Nexal Legal multi-tenant architecture.

What it does:
  1. Validates input
  2. Creates the firm record in platform.db
  3. Creates the firm directory under /data/firms/<FIRM_ID>/
  4. Clones the template database into that directory
  5. Creates the workspace record in platform.db
  6. Returns a workspace details dict

Usage (on VPS or in admin tooling):
    from provisioning import provision_firm
    result = provision_firm("FIRM001", "Smith & Partners LLP")
    print(result)

Architecture:
  /data/
    platform.db                        <- PlatformDB (firms + workspaces)
    template/
      solicitor_ledger.db              <- clean template DB (never modified)
    firms/
      FIRM001/
        solicitor_ledger.db            <- Firm A's live DB
      FIRM002/
        solicitor_ledger.db            <- Firm B's live DB
      FIRM003/
        solicitor_ledger.db            <- Firm C's live DB
"""

import os
import re
import shutil
import logging
from datetime import datetime
from platform_db import PlatformDB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / path helpers
# ---------------------------------------------------------------------------

DB_FILENAME = "solicitor_ledger.db"


def _data_root() -> str:
    """Return the root data directory (configurable via env var)."""
    root = os.environ.get(
        "NEXAL_DATA_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    )
    os.makedirs(root, exist_ok=True)
    return root


def _template_db_path() -> str:
    """Return the path of the clean template DB."""
    path = os.path.join(_data_root(), "template", DB_FILENAME)
    return path


def _firm_dir(firm_id: str) -> str:
    """Return the directory path for a given firm."""
    return os.path.join(_data_root(), "firms", firm_id)


def _firm_db_path(firm_id: str) -> str:
    """Return the database path for a given firm."""
    return os.path.join(_firm_dir(firm_id), DB_FILENAME)


def _workspace_id(firm_id: str) -> str:
    """Generate a deterministic workspace ID from the firm ID."""
    return f"WS-{firm_id}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_FIRM_ID_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")


def _validate_firm_id(firm_id: str):
    if not firm_id or not _FIRM_ID_RE.match(firm_id):
        raise ValueError(
            f"Invalid firm_id '{firm_id}'. "
            "Must be 3-32 uppercase alphanumeric characters, underscores or hyphens."
        )


def _validate_firm_name(firm_name: str):
    if not firm_name or len(firm_name.strip()) < 2:
        raise ValueError("firm_name must be at least 2 characters.")
    if len(firm_name) > 200:
        raise ValueError("firm_name must be 200 characters or fewer.")


# ---------------------------------------------------------------------------
# Template initialisation
# ---------------------------------------------------------------------------

def ensure_template_db():
    """
    Ensure a clean template database exists at /data/template/solicitor_ledger.db.
    If it does not exist, create a fresh initialised one using the Database class.
    This function is idempotent.
    """
    template_path = _template_db_path()
    template_dir = os.path.dirname(template_path)
    os.makedirs(template_dir, exist_ok=True)

    if not os.path.exists(template_path):
        logger.info("Template DB not found; creating at %s", template_path)
        from database import Database
        db = Database(db_path=template_path)
        # Initialise schema with no data
        del db
        logger.info("Template DB created successfully at %s", template_path)
    else:
        logger.info("Template DB already exists at %s", template_path)

    return template_path


# ---------------------------------------------------------------------------
# Core provisioning function
# ---------------------------------------------------------------------------

def provision_firm(
    firm_id: str,
    firm_name: str,
    platform_db_path: str = None,
) -> dict:
    """
    Provision a new law firm in the Nexal Legal multi-tenant system.

    Steps:
      1. Validate inputs
      2. Check firm does not already exist
      3. Ensure template DB exists
      4. Create firm directory
      5. Clone template DB into firm directory
      6. Register firm in platform.db
      7. Register workspace in platform.db
      8. Return workspace details

    Args:
        firm_id:          Unique firm identifier, e.g. 'FIRM001'
        firm_name:        Human-readable firm name
        platform_db_path: Optional override for platform.db path

    Returns:
        dict with keys:
            firm_id, firm_name, workspace_id, workspace_name,
            db_path, status, created_at
    """
    # 1. Validate
    firm_id = firm_id.strip().upper()
    firm_name = firm_name.strip()
    _validate_firm_id(firm_id)
    _validate_firm_name(firm_name)

    # 2. Platform DB
    pdb = PlatformDB(db_path=platform_db_path)

    if pdb.firm_exists(firm_id):
        raise ValueError(f"Firm '{firm_id}' already exists in platform.db.")

    # 3. Ensure template exists
    template_path = ensure_template_db()

    # 4. Create firm directory
    firm_directory = _firm_dir(firm_id)
    os.makedirs(firm_directory, exist_ok=True)
    logger.info("Firm directory created: %s", firm_directory)

    # 5. Clone template DB
    firm_db = _firm_db_path(firm_id)
    if os.path.exists(firm_db):
        logger.warning("Firm DB already exists at %s – skipping clone", firm_db)
    else:
        shutil.copy2(template_path, firm_db)
        logger.info("Template cloned to %s", firm_db)

    # 6. Register firm
    firm_record = pdb.create_firm(
        firm_id=firm_id,
        firm_name=firm_name,
        db_path=firm_db,
    )

    # 7. Register workspace
    ws_id = _workspace_id(firm_id)
    ws_name = f"{firm_name} Workspace"
    workspace_record = pdb.create_workspace(
        workspace_id=ws_id,
        firm_id=firm_id,
        workspace_name=ws_name,
        db_path=firm_db,
    )

    logger.info(
        "Firm '%s' (%s) provisioned successfully. Workspace: %s, DB: %s",
        firm_id, firm_name, ws_id, firm_db
    )

    return {
        "firm_id": firm_id,
        "firm_name": firm_name,
        "workspace_id": ws_id,
        "workspace_name": ws_name,
        "db_path": firm_db,
        "status": "active",
        "created_at": firm_record.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Utility: deprovision (for testing / admin use only)
# ---------------------------------------------------------------------------

def deprovision_firm(firm_id: str, platform_db_path: str = None,
                     delete_files: bool = False) -> dict:
    """
    Archive a firm (set status='archived') and optionally delete its DB files.
    This is a SOFT operation by default – it never deletes platform records.

    Args:
        firm_id:          The firm to deprovision
        platform_db_path: Optional override for platform.db path
        delete_files:     If True, physically remove the firm's DB directory.
                          USE WITH EXTREME CAUTION. Irreversible.

    Returns:
        dict with firm_id and new status
    """
    firm_id = firm_id.strip().upper()
    pdb = PlatformDB(db_path=platform_db_path)

    if not pdb.firm_exists(firm_id):
        raise ValueError(f"Firm '{firm_id}' not found.")

    # Archive workspace(s)
    for ws in pdb.list_workspaces(firm_id=firm_id):
        pdb.update_workspace_status(ws["workspace_id"], "archived")

    # Archive firm
    pdb.update_firm_status(firm_id, "archived")

    if delete_files:
        firm_directory = _firm_dir(firm_id)
        if os.path.exists(firm_directory):
            shutil.rmtree(firm_directory)
            logger.warning("Firm directory DELETED: %s", firm_directory)

    logger.info("Firm '%s' deprovisioned (archived).", firm_id)
    return {"firm_id": firm_id, "status": "archived"}
