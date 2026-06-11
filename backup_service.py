"""
Automatic Backup System for Solicitor Web.
SRA-compliant full data backup: database, config, audit trail.
Implements AES-256 encryption for secure backup storage.
"""
import os
import sys
import sqlite3
import shutil
import zipfile
import tempfile
import threading
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
from pathlib import Path

# AES-256 encryption using cryptography library
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False

ENCRYPTED_EXTENSION = '.enc'

def _get_data_dir() -> str:
    """Return the persistent user data directory for the application."""
    if getattr(sys, 'frozen', False):
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'SolicitorLedger'
        )
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(base, exist_ok=True)
    return base


def _default_db_path() -> str:
    return os.path.join(_get_data_dir(), 'solicitor_ledger.db')


def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


LOCAL_BACKUP_DIR = os.path.join(_get_data_dir(), 'backups', 'local')


def _onedrive_path() -> Optional[str]:
    """Detect OneDrive folder from environment."""
    for key in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        p = os.environ.get(key)
        if p and os.path.isdir(p):
            return p
    local = os.environ.get('LOCALAPPDATA', '')
    if local:
        od = os.path.join(local, 'Microsoft', 'OneDrive')
        if os.path.isdir(od):
            return od
    return None


ONEDRIVE_BACKUP_DIR = None  # Set at runtime


def get_active_cloud_backup_dir(db_path: str = None) -> Optional[str]:
    """
    Return the active cloud/network backup destination.
    Priority:
      1. Custom location saved in system_config ('custom_backup_dir'), if it exists on disk.
      2. Auto-detected OneDrive location.
    Returns None if neither is configured or reachable.
    """
    db_path = db_path or _default_db_path()
    custom = (_get_config(db_path, 'custom_backup_dir', '') or '').strip()
    if custom and os.path.isdir(custom):
        return custom
    return get_onedrive_backup_dir()


def set_custom_backup_dir(db_path: str, path: str) -> bool:
    """Persist a custom cloud/network backup directory to system_config."""
    try:
        _set_config(db_path, 'custom_backup_dir', path, 'Custom cloud/network backup destination')
        return True
    except Exception:
        return False


def clear_custom_backup_dir(db_path: str):
    """Remove custom backup directory (reverts to OneDrive auto-detection)."""
    _set_config(db_path, 'custom_backup_dir', '', 'Custom cloud/network backup destination')


def get_custom_backup_dir_raw(db_path: str = None) -> str:
    """Return the raw stored custom_backup_dir value (empty string if not set)."""
    db_path = db_path or _default_db_path()
    return (_get_config(db_path, 'custom_backup_dir', '') or '').strip()


def get_onedrive_backup_dir() -> Optional[str]:
    global ONEDRIVE_BACKUP_DIR
    if ONEDRIVE_BACKUP_DIR is not None:
        return ONEDRIVE_BACKUP_DIR
    base = _onedrive_path()
    if base:
        path = os.path.join(base, 'SolicitorWebBackups', 'Nightly')
        os.makedirs(path, exist_ok=True)
        ONEDRIVE_BACKUP_DIR = path
        return path
    return None


BACKUP_LOCK = threading.Lock()
RETENTION_DAYS = 30
USB_BACKUP_INTERVAL_DAYS = 30


def _get_config(db_path: str, key: str, default: str = None) -> Optional[str]:
    """Read value from system_config table."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def _set_config(db_path: str, key: str, value: str, description: str = None):
    """Write value to system_config table."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_config (key, value, description, updated_date)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_date = CURRENT_TIMESTAMP
        """, (key, value, description or ''))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_or_create_encryption_key(db_path: str) -> Optional[bytes]:
    """
    Get AES-256 encryption key. Priority:
    1. BACKUP_ENCRYPTION_KEY environment variable
    2. Stored key in system_config (backup_encryption_key)
    3. Auto-generate new key and persist to system_config
    
    Returns 32-byte key or None only if encryption library unavailable.
    """
    key_str = os.environ.get('BACKUP_ENCRYPTION_KEY', '').strip()
    if not key_str and db_path and os.path.isfile(db_path):
        key_str = _get_config(db_path, 'backup_encryption_key', '') or ''
    if not key_str:
        key_str = secrets.token_urlsafe(32)
        if db_path and os.path.isfile(db_path):
            _set_config(db_path, 'backup_encryption_key', key_str, 'Auto-generated backup encryption key')
        os.environ['BACKUP_ENCRYPTION_KEY'] = key_str
    if not key_str:
        return None
    return hashlib.sha256(key_str.encode('utf-8')).digest()


def _get_encryption_key(db_path: str = None) -> Optional[bytes]:
    """
    Get AES-256 encryption key. Auto-creates if not set.
    Returns 32-byte key or None if encryption library unavailable.
    """
    db_path = db_path or _default_db_path()
    return _get_or_create_encryption_key(db_path)


def _encrypt_file(input_path: str, output_path: str, key: bytes) -> bool:
    """
    Encrypt file using AES-256-CBC.
    Prepends 16-byte IV to output file.
    Returns True on success.
    """
    if not ENCRYPTION_AVAILABLE:
        return False
    try:
        iv = secrets.token_bytes(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        with open(input_path, 'rb') as f_in:
            plaintext = f_in.read()
        
        # PKCS7 padding to block size (16 bytes)
        pad_len = 16 - (len(plaintext) % 16)
        plaintext += bytes([pad_len] * pad_len)
        
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        
        with open(output_path, 'wb') as f_out:
            f_out.write(iv + ciphertext)
        
        return True
    except Exception:
        return False


def _decrypt_file(input_path: str, output_path: str, key: bytes) -> bool:
    """
    Decrypt AES-256-CBC encrypted file.
    Expects 16-byte IV prepended to ciphertext.
    Returns True on success.
    """
    if not ENCRYPTION_AVAILABLE:
        return False
    try:
        with open(input_path, 'rb') as f_in:
            data = f_in.read()
        
        if len(data) < 32:  # IV (16) + at least one block (16)
            return False
        
        iv = data[:16]
        ciphertext = data[16:]
        
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        
        # Remove PKCS7 padding
        pad_len = plaintext[-1]
        if pad_len > 16 or pad_len == 0:
            return False
        plaintext = plaintext[:-pad_len]
        
        with open(output_path, 'wb') as f_out:
            f_out.write(plaintext)
        
        return True
    except Exception:
        return False


def is_encryption_configured() -> bool:
    """Check if encryption key is available (env or auto-generated)."""
    key = _get_encryption_key()
    return key is not None


def _ensure_dirs():
    os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)
    od = get_onedrive_backup_dir()
    if od:
        os.makedirs(od, exist_ok=True)


def _create_db_snapshot(db_path: str, dest_path: str) -> bool:
    """Create a consistent DB copy using SQLite backup API."""
    try:
        src = sqlite3.connect(db_path, timeout=30)
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = sqlite3.connect(dest_path)
        src.backup(dst)
        src.close()
        dst.close()
        return True
    except Exception as e:
        # Fallback: simple file copy (may be inconsistent if DB is in use)
        try:
            shutil.copy2(db_path, dest_path)
            return True
        except Exception:
            return False


def _verify_zip(path: str) -> bool:
    """Verify ZIP integrity."""
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            bad = zf.testzip()
            return bad is None
    except Exception:
        return False


def create_backup(db_path: str = None, audit_callback=None) -> Tuple[bool, str, Optional[str]]:
    """
    Create full encrypted backup. Returns (success, message, backup_path).
    Uses lock to prevent simultaneous backups.
    audit_callback(action, details) - called to log audit entries.
    
    SECURITY: Backups are encrypted using AES-256 before being written to disk.
    Encryption key is read from BACKUP_ENCRYPTION_KEY environment variable.
    """
    if not BACKUP_LOCK.acquire(blocking=False):
        return False, "Another backup is already in progress.", None

    try:
        db_path = db_path or _default_db_path()
        if not ENCRYPTION_AVAILABLE:
            msg = "Backup failed – encryption error. Install 'cryptography' package."
            if audit_callback:
                audit_callback('BACKUP_ENCRYPTION_UNAVAILABLE', msg)
            return False, msg, None

        encryption_key = _get_encryption_key(db_path)
        if not encryption_key:
            msg = "Backup failed – encryption error."
            if audit_callback:
                audit_callback('BACKUP_ENCRYPTION_ERROR', msg)
            return False, msg, None
        if not os.path.isfile(db_path):
            return False, f"Database not found: {db_path}", None

        _ensure_dirs()
        now = datetime.now()
        fname_zip = f"backup_{now.strftime('%Y-%m-%d_%H%M')}.zip"
        fname_enc = fname_zip + ENCRYPTED_EXTENSION
        temp_dir = tempfile.mkdtemp()
        backup_path = None

        try:
            # Snapshot database
            snap_db = os.path.join(temp_dir, 'solicitor_ledger.db')
            if not _create_db_snapshot(db_path, snap_db):
                return False, "Failed to create database snapshot.", None

            # Include WAL/journal if present (for consistency)
            for suf in ('-journal', '-wal', '-shm'):
                p = db_path + suf
                if os.path.isfile(p):
                    shutil.copy2(p, os.path.join(temp_dir, os.path.basename(db_path) + suf))

            # Create ZIP in temp directory (not final destination)
            temp_zip = os.path.join(temp_dir, 'backup.zip')
            with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(temp_dir):
                    for f in files:
                        if f.endswith('.zip'):
                            continue  # Don't include the zip itself
                        fp = os.path.join(root, f)
                        zf.write(fp, os.path.relpath(fp, temp_dir))

            if not _verify_zip(temp_zip):
                return False, "ZIP verification failed after creation.", None

            # Encrypt the ZIP file
            local_enc = os.path.join(LOCAL_BACKUP_DIR, fname_enc)
            if not _encrypt_file(temp_zip, local_enc, encryption_key):
                return False, "Encryption failed. Backup not created.", None

            backup_path = local_enc

            # Copy encrypted backup to cloud/network destination
            od_dir = get_active_cloud_backup_dir(db_path)
            if od_dir:
                od_enc = os.path.join(od_dir, fname_enc)
                shutil.copy2(local_enc, od_enc)
                if not os.path.isfile(od_enc):
                    if audit_callback:
                        audit_callback('OneDrive save failed', f"Copy to {od_dir} failed")
                elif audit_callback:
                    audit_callback('Encrypted backup saved to OneDrive', od_enc)

            if audit_callback:
                audit_callback('ENCRYPTED_BACKUP_CREATED', f"Encrypted backup created: {fname_enc}")

            # Retention cleanup
            deleted = cleanup_old_backups(audit_callback)
            if deleted and audit_callback:
                audit_callback('Automatic cleanup of old backups', f"Removed {deleted} backup(s) older than {RETENTION_DAYS} days")

            return True, "Backup created successfully.", local_enc

        finally:
            # Clean up temp directory - no plaintext files left on disk
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        return False, str(e), None
    finally:
        BACKUP_LOCK.release()


def cleanup_old_backups(audit_callback=None) -> int:
    """Delete backups older than RETENTION_DAYS. Returns count deleted."""
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    deleted = 0

    for folder in (LOCAL_BACKUP_DIR, get_onedrive_backup_dir() or ''):
        if not folder or not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            # Handle both legacy .zip and encrypted .zip.enc files
            if not f.startswith('backup_'):
                continue
            if not (f.endswith('.zip') or f.endswith(ENCRYPTED_EXTENSION)):
                continue
            fp = os.path.join(folder, f)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                if mtime < cutoff:
                    os.remove(fp)
                    deleted += 1
            except OSError:
                pass

    return deleted


def get_usb_drives() -> List[str]:
    """Return list of removable drive letters (e.g. ['E:', 'F:'])."""
    drives = []
    try:
        import string
        for letter in string.ascii_uppercase:
            path = f"{letter}:\\"
            if os.path.exists(path):
                try:
                    if sys.platform == 'win32':
                        import ctypes
                        drive_type = ctypes.windll.kernel32.GetDriveTypeW(path)  # type: ignore
                        if drive_type == 2:  # DRIVE_REMOVABLE
                            drives.append(path)
                except Exception:
                    pass
    except Exception:
        pass
    return drives


def get_latest_backup_path() -> Optional[str]:
    """Return path to most recent backup in local folder (encrypted or legacy)."""
    if not os.path.isdir(LOCAL_BACKUP_DIR):
        return None
    latest = None
    latest_mtime = 0
    for f in os.listdir(LOCAL_BACKUP_DIR):
        if not f.startswith('backup_'):
            continue
        # Handle both legacy .zip and encrypted .zip.enc files
        if not (f.endswith('.zip') or f.endswith(ENCRYPTED_EXTENSION)):
            continue
        fp = os.path.join(LOCAL_BACKUP_DIR, f)
        try:
            m = os.path.getmtime(fp)
            if m > latest_mtime:
                latest_mtime = m
                latest = fp
        except OSError:
            pass
    return latest


def copy_to_usb(audit_callback=None) -> Tuple[bool, str]:
    """
    Copy latest backup to USB. Returns (success, message).
    """
    if not BACKUP_LOCK.acquire(blocking=False):
        return False, "Backup in progress. Wait and retry."

    try:
        drives = get_usb_drives()
        if not drives:
            if audit_callback:
                audit_callback('USB backup failure', 'No USB drive detected')
            return False, "No USB drive detected. Please connect a USB drive and try again."

        latest = get_latest_backup_path()
        if not latest or not os.path.isfile(latest):
            return False, "No backup found. Create a backup first."

        fname = os.path.basename(latest)
        success_drive = None

        for drive in drives:
            dest_dir = os.path.join(drive, 'SolicitorWebBackups', 'Offline')
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, fname)
            try:
                shutil.copy2(latest, dest)
                if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(latest):
                    success_drive = drive
                    break
            except Exception:
                continue

        if success_drive:
            if audit_callback:
                audit_callback('USB backup completed', f"Copied to {success_drive}SolicitorWebBackups\\Offline\\")
            return True, "USB Backup Completed Successfully."
        else:
            if audit_callback:
                audit_callback('USB backup failure', 'Copy verification failed')
            return False, "Failed to copy to USB. Check drive permissions and try again."

    finally:
        BACKUP_LOCK.release()


def get_last_backup_time() -> Optional[datetime]:
    """Last backup mtime from local folder."""
    p = get_latest_backup_path()
    if p:
        try:
            return datetime.fromtimestamp(os.path.getmtime(p))
        except OSError:
            pass
    return None


def restore_backup(backup_path: str, db_path: str = None, audit_callback=None) -> Tuple[bool, str]:
    """
    Restore database from backup file.
    Handles both encrypted (.enc) and legacy (.zip) backups.
    
    Returns (success, message).
    """
    if not BACKUP_LOCK.acquire(blocking=False):
        return False, "Another backup operation is in progress."
    
    try:
        if not os.path.isfile(backup_path):
            return False, f"Backup file not found: {backup_path}"
        
        db_path = db_path or _default_db_path()
        temp_dir = tempfile.mkdtemp()
        
        try:
            is_encrypted = backup_path.endswith(ENCRYPTED_EXTENSION)
            
            if is_encrypted:
                # Decrypt the backup first
                encryption_key = _get_encryption_key(db_path)
                if not encryption_key:
                    return False, "Backup failed – encryption error. Cannot decrypt backup."
                
                if not ENCRYPTION_AVAILABLE:
                    return False, "Encryption library not available. Install 'cryptography' package."
                
                # Decrypt to temp zip file
                temp_zip = os.path.join(temp_dir, 'decrypted_backup.zip')
                if not _decrypt_file(backup_path, temp_zip, encryption_key):
                    return False, "Decryption failed. Wrong encryption key or corrupted backup."
                
                zip_path = temp_zip
            else:
                # Legacy unencrypted backup
                zip_path = backup_path
            
            # Verify ZIP integrity
            if not _verify_zip(zip_path):
                return False, "Backup file is corrupted or invalid."
            
            # Extract database from ZIP
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Find the database file in the archive
                db_files = [n for n in zf.namelist() if n.endswith('.db')]
                if not db_files:
                    return False, "No database file found in backup."
                
                # Extract to temp directory
                zf.extractall(temp_dir)
            
            # Find extracted database
            extracted_db = os.path.join(temp_dir, db_files[0])
            if not os.path.isfile(extracted_db):
                return False, "Failed to extract database from backup."
            
            # Backup current database before replacing
            if os.path.isfile(db_path):
                backup_current = db_path + '.pre_restore'
                shutil.copy2(db_path, backup_current)
            
            # Replace database
            shutil.copy2(extracted_db, db_path)
            
            # Remove WAL/journal files to ensure clean state
            for suf in ('-journal', '-wal', '-shm'):
                p = db_path + suf
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            
            if audit_callback:
                audit_callback('BACKUP_RESTORED', f"Database restored from: {os.path.basename(backup_path)}")
            
            return True, "Backup restored successfully. Please restart the application."
            
        finally:
            # Clean up temp directory - no plaintext files left on disk
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    except Exception as e:
        return False, f"Restore failed: {str(e)}"
    finally:
        BACKUP_LOCK.release()


def list_available_backups() -> List[dict]:
    """
    List all available backups with metadata.
    Returns list of dicts with 'path', 'filename', 'date', 'encrypted', 'size'.
    """
    backups = []
    if not os.path.isdir(LOCAL_BACKUP_DIR):
        return backups
    
    for f in os.listdir(LOCAL_BACKUP_DIR):
        if not f.startswith('backup_'):
            continue
        if not (f.endswith('.zip') or f.endswith(ENCRYPTED_EXTENSION)):
            continue
        
        fp = os.path.join(LOCAL_BACKUP_DIR, f)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(fp))
            size = os.path.getsize(fp)
            backups.append({
                'path': fp,
                'filename': f,
                'date': mtime,
                'encrypted': f.endswith(ENCRYPTED_EXTENSION),
                'size': size,
            })
        except OSError:
            pass
    
    # Sort by date descending (newest first)
    backups.sort(key=lambda x: x['date'], reverse=True)
    return backups
