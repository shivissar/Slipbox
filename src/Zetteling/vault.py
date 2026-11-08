"""
vault.py — Encryption for locked notes (5.1).

A locked note keeps its frontmatter (title, tags, kind, threads, dates) in
plain text; only the body is encrypted. The scheme:

  * One random 256-bit data key, made once when locked notes are set up.
  * That data key is stored wrapped (encrypted) twice: under a key derived
    from the password with Argon2id (RFC 9106), and under a key derived from
    a random 256-bit recovery key with HKDF-SHA-256 (RFC 5869). Both
    wrappings use AES-256-GCM (NIST SP 800-38D).
  * Each time a locked note is saved, a fresh 256-bit salt is drawn and a
    one-use AES-256 key is derived from the data key with HKDF-SHA-256. The
    body is encrypted with AES-256-GCM under that key with a random 96-bit
    nonce, binding the vault id and note id as associated data. One key per
    encryption keeps well inside GCM's limits on random nonces.
  * The wrapped keys (never the password or data key) are kept in
    .zetteling-vault.json in the notes folder, and a copy travels inside
    every locked note, so a locked note can be opened with the password even
    if the key file is lost.

The data key lives only in this process's memory while locked notes are
open. Python cannot guarantee memory is wiped; quitting the app ends the
process.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import base64
import json
import os
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:  # Argon2id needs a recent cryptography/OpenSSL; scrypt is the fallback.
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    HAVE_ARGON2 = True
except Exception:  # pragma: no cover - depends on the bundled OpenSSL
    Argon2id = None
    HAVE_ARGON2 = False
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VAULT_FORMAT = "zetteling-vault-v1"
NOTE_FORMAT = "zetteling-locked-v1"
BEGIN = "-----BEGIN ZETTELING LOCKED NOTE-----"
END = "-----END ZETTELING LOCKED NOTE-----"
KEYFILE_NAME = ".zetteling-vault.json"
MIN_PASSWORD = 10

# RFC 9106 section 4 recommends two settings. Since 5.2 new passwords use
# the SECOND RECOMMENDED one (64 MiB, t=3, p=4), which the RFC calls safe for
# all environments. The FIRST (2 GiB, t=1, p=4) took 2 GiB of memory on every
# unlock and every wrong guess; vaults made with it are moved to the second
# the next time they're unlocked. The settings used are stored per vault.
ARGON2_STRONG = {"name": "argon2id", "memory_kib": 2 ** 21, "iterations": 1, "lanes": 4}
ARGON2_LIGHT = {"name": "argon2id", "memory_kib": 2 ** 16, "iterations": 3, "lanes": 4}
ARGON2_DEFAULT = ARGON2_LIGHT
# Only if Argon2id is missing from the bundled library: RFC 7914 scrypt at
# N=2^17, r=8 (128 MiB), OWASP's recommended minimum.
SCRYPT_FALLBACK = {"name": "scrypt", "n": 2 ** 17, "r": 8, "p": 1}


class VaultError(Exception):
    """Base class; the message is safe to show to the user."""


class NotSetUp(VaultError):
    pass


class VaultLocked(VaultError):
    pass


class WrongPassword(VaultError):
    pass


class Damaged(VaultError):
    pass


_lock = threading.RLock()
_dek = None          # the data key, bytes, only while unlocked
_header = None       # the vault header in use (dict)
_last_activity = time.monotonic()


# ---------------------------------------------------------------- helpers

def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _unb64(s):
    return base64.b64decode(s.encode("ascii"), validate=True)


def _notes_dir():
    from . import notes
    return Path(notes.NOTES_DIR)


def keyfile_path():
    return _notes_dir() / KEYFILE_NAME


def _derive_password_key(password, kdf):
    pw = password.encode("utf-8")
    salt = _unb64(kdf["salt"])
    if kdf["name"] == "argon2id":
        if not HAVE_ARGON2:
            raise VaultError("This copy of Zetteling can't use Argon2id, which these notes need.")
        return Argon2id(salt=salt, length=32, iterations=kdf["iterations"],
                        lanes=kdf["lanes"], memory_cost=kdf["memory_kib"]).derive(pw)
    if kdf["name"] == "scrypt":
        return Scrypt(salt=salt, length=32, n=kdf["n"], r=kdf["r"], p=kdf["p"]).derive(pw)
    raise VaultError("Unknown key derivation: %s" % kdf.get("name"))


def _recovery_kek(recovery_bytes, vault_id):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=vault_id,
                info=b"zetteling recovery v1").derive(recovery_bytes)


def _wrap(kek, dek, slot, vault_id):
    nonce = os.urandom(12)
    aad = ("%s|%s|" % (VAULT_FORMAT, slot)).encode() + vault_id
    return {"nonce": _b64(nonce), "ct": _b64(AESGCM(kek).encrypt(nonce, dek, aad))}


def _unwrap(kek, wrapped, slot, vault_id):
    aad = ("%s|%s|" % (VAULT_FORMAT, slot)).encode() + vault_id
    return AESGCM(kek).decrypt(_unb64(wrapped["nonce"]), _unb64(wrapped["ct"]), aad)


def _note_aad(vault_id_b64, note_id):
    return ("%s|%s|%s" % (NOTE_FORMAT, vault_id_b64, note_id)).encode()


def format_recovery(raw):
    s = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))


def parse_recovery(text):
    s = "".join(ch for ch in text.upper() if ch.isalnum())
    s = s.replace("0", "O").replace("1", "I").replace("8", "B")
    s += "=" * (-len(s) % 8)
    try:
        raw = base64.b32decode(s)
    except Exception:
        raise WrongPassword("That recovery key isn't in the right format.")
    if len(raw) != 32:
        raise WrongPassword("That recovery key is the wrong length.")
    return raw


def _write_keyfile(header):
    path = keyfile_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(header, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_keyfile():
    path = keyfile_path()
    if not path.exists():
        return None
    try:
        header = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return header if header.get("format") == VAULT_FORMAT else None


def _any_locked_in_index():
    """Cheap check before scanning note files for a vault header."""
    try:
        from . import db
        return bool(db.locked_ids())
    except Exception:
        return False


def _header_from_notes():
    """Recover the vault header from any locked note (used if the key file
    has gone missing)."""
    for path in sorted(_notes_dir().glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if BEGIN not in text:
            continue
        try:
            payload = _parse_armour(text)
        except Damaged:
            continue
        vault = payload.get("vault")
        if isinstance(vault, dict) and vault.get("format") == VAULT_FORMAT:
            return vault
    return None


def load_header():
    header = _read_keyfile()
    if header is None and _any_locked_in_index():
        header = _header_from_notes()
        if header is not None:
            try:
                _write_keyfile(header)
            except OSError:
                pass
    return header


# ---------------------------------------------------------------- state

def is_set_up():
    return load_header() is not None


def is_unlocked():
    return _dek is not None


def touch():
    global _last_activity
    _last_activity = time.monotonic()


def idle_seconds():
    return time.monotonic() - _last_activity


def autolock_if_idle(minutes):
    """Lock if open and idle for longer than `minutes` (0 = never)."""
    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes > 0 and is_unlocked() and idle_seconds() > minutes * 60:
        lock()
        return True
    return False


def lock():
    global _dek, _header
    with _lock:
        _dek = None
        _header = None


def kdf_description(header=None):
    header = header or _header or load_header()
    if not header:
        return ""
    k = header["kdf"]
    if k["name"] == "argon2id":
        return "Argon2id, %d MiB, %d pass%s, %d lanes" % (
            k["memory_kib"] // 1024, k["iterations"], "" if k["iterations"] == 1 else "es", k["lanes"])
    return "scrypt, N=2^%d, r=%d, p=%d" % (k["n"].bit_length() - 1, k["r"], k["p"])


# ---------------------------------------------------------------- setup and keys

def _check_password(password):
    if len(password or "") < MIN_PASSWORD:
        raise VaultError("Use at least %d characters. A phrase of four or five words is easiest to remember." % MIN_PASSWORD)


def _derive_new(password, kdf=None):
    """Derive a password key with the standard setting (or `kdf`, in tests).
    Returns (kdf, key)."""
    if kdf:
        candidates = [kdf]
    else:
        candidates = [ARGON2_DEFAULT] if HAVE_ARGON2 else [SCRYPT_FALLBACK]
    last = None
    for params in candidates:
        chosen = dict(params, salt=_b64(os.urandom(16)))
        try:
            return chosen, _derive_password_key(password, chosen)
        except (MemoryError, OverflowError) as e:
            last = e
            continue
        except Exception as e:  # OpenSSL allocation failures surface as other types
            if "memory" in str(e).lower() or "alloc" in str(e).lower():
                last = e
                continue
            raise
    raise VaultError("This Mac couldn't run the password key derivation (%s)." % last)


def setup(password, kdf=None):
    """Create the vault. Returns the recovery key (shown once, never stored)."""
    global _dek, _header
    _check_password(password)
    with _lock:
        if is_set_up():
            raise VaultError("Locked notes are already set up.")
        kdf, kek = _derive_new(password, kdf)
        dek = os.urandom(32)
        vault_id = os.urandom(16)
        recovery = os.urandom(32)
        header = {
            "format": VAULT_FORMAT,
            "vault_id": _b64(vault_id),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cipher": "AES-256-GCM",
            "kdf": kdf,
            "password_wrap": _wrap(kek, dek, "password", vault_id),
            "recovery_wrap": _wrap(_recovery_kek(recovery, vault_id), dek, "recovery", vault_id),
        }
        _write_keyfile(header)
        _dek, _header = dek, header
        touch()
        return format_recovery(recovery)


def unlock(password):
    global _dek, _header
    header = load_header()
    if header is None:
        raise NotSetUp("Locked notes aren't set up yet.")
    vault_id = _unb64(header["vault_id"])
    kek = _derive_password_key(password or "", header["kdf"])
    try:
        dek = _unwrap(kek, header["password_wrap"], "password", vault_id)
    except InvalidTag:
        raise WrongPassword("That password isn't right.") from None
    with _lock:
        _dek, _header = dek, header
        touch()
    if _needs_new_settings(header):
        _move_to_default_settings(password)
    return True


def _needs_new_settings(header):
    k = header.get("kdf") or {}
    if not HAVE_ARGON2:
        return False
    if k.get("name") != "argon2id":
        return True
    return (k.get("memory_kib"), k.get("iterations"), k.get("lanes")) != (
        ARGON2_DEFAULT["memory_kib"], ARGON2_DEFAULT["iterations"], ARGON2_DEFAULT["lanes"])


def _move_to_default_settings(password):
    """Re-wrap the data key under the same password with the standard
    setting (5.2: vaults made with the 2 GiB setting). Note bodies don't
    change; the header copy inside each locked note is updated. A failure
    leaves the vault as it was."""
    global _header
    try:
        with _lock:
            new = _rewrap_password(_header, _dek, password)
            _write_keyfile(new)
            _header = new
        refresh_note_headers()
    except Exception as e:  # never block an unlock on this
        print("[vault] couldn't move to the standard password setting: %s" % e)


def _rewrap_password(header, dek, new_password):
    vault_id = _unb64(header["vault_id"])
    # Always the standard setting, never the stored one, so a tampered key
    # file can't talk a password change into weak settings.
    kdf, kek = _derive_new(new_password)
    new = dict(header)
    new["kdf"] = kdf
    new["password_wrap"] = _wrap(kek, dek, "password", vault_id)
    return new


def change_password(old_password, new_password):
    """Re-wrap the data key under a new password. Note bodies don't change."""
    global _header
    _check_password(new_password)
    unlock(old_password)
    with _lock:
        new = _rewrap_password(_header, _dek, new_password)
        _write_keyfile(new)
        _header = new
    refresh_note_headers()
    return True


def recover(recovery_text, new_password):
    """Open the vault with the recovery key and set a new password."""
    global _dek, _header
    _check_password(new_password)
    header = load_header()
    if header is None:
        raise NotSetUp("Locked notes aren't set up yet.")
    raw = parse_recovery(recovery_text)
    vault_id = _unb64(header["vault_id"])
    try:
        dek = _unwrap(_recovery_kek(raw, vault_id), header["recovery_wrap"], "recovery", vault_id)
    except InvalidTag:
        raise WrongPassword("That recovery key doesn't match these notes.")
    with _lock:
        new = _rewrap_password(header, dek, new_password)
        _write_keyfile(new)
        _dek, _header = dek, new
        touch()
    refresh_note_headers()
    return True


# ---------------------------------------------------------------- note bodies

def _armour(payload):
    blob = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return "\n".join([BEGIN, *textwrap.wrap(blob, 76), END]) + "\n"


def _parse_armour(text):
    try:
        start = text.index(BEGIN) + len(BEGIN)
        stop = text.index(END, start)
        blob = "".join(text[start:stop].split())
        payload = json.loads(base64.b64decode(blob, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Damaged("This locked note is damaged or was changed outside Zetteling.")
    if payload.get("format") != NOTE_FORMAT:
        raise Damaged("This locked note uses a format this version doesn't know.")
    return payload


def looks_locked(text):
    return BEGIN in (text or "")


def encrypt_body(note_id, plaintext):
    with _lock:
        if _dek is None:
            raise VaultLocked("Locked notes are closed. Enter your password to save this note.")
        dek, header = _dek, _header
    salt, nonce = os.urandom(32), os.urandom(12)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"zetteling note v1").derive(dek)
    ct = AESGCM(key).encrypt(nonce, (plaintext or "").encode("utf-8"), _note_aad(header["vault_id"], note_id))
    return _armour({
        "format": NOTE_FORMAT,
        "cipher": "AES-256-GCM",
        "kdf": "HKDF-SHA-256",
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ct": _b64(ct),
        "vault": header,
    })


def decrypt_body(note_id, armoured):
    payload = _parse_armour(armoured)
    with _lock:
        if _dek is None:
            raise VaultLocked("Locked notes are closed.")
        dek, header = _dek, _header
    vault_id = (payload.get("vault") or {}).get("vault_id")
    if vault_id and vault_id != header["vault_id"]:
        raise Damaged("This note was locked with a different set of keys.")
    try:
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=_unb64(payload["salt"]),
                   info=b"zetteling note v1").derive(dek)
        plain = AESGCM(key).decrypt(_unb64(payload["nonce"]), _unb64(payload["ct"]),
                                    _note_aad(header["vault_id"], note_id))
    except (InvalidTag, KeyError, ValueError):
        raise Damaged("This locked note can't be opened: it was changed outside Zetteling or is damaged.")
    return plain.decode("utf-8")


def rewrap_armour(armoured):
    """Replace the vault header carried inside a locked note, keeping the
    encrypted body as it is (after a password change)."""
    payload = _parse_armour(armoured)
    with _lock:
        if _header is None:
            raise VaultLocked("Locked notes are closed.")
        payload["vault"] = _header
    return _armour(payload)


def refresh_note_headers():
    """After a password change, update the header copy in every locked note
    so the old password no longer opens them from this folder."""
    for path in sorted(_notes_dir().glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            if BEGIN not in text:
                continue
            start = text.index(BEGIN)
            end = text.index(END, start) + len(END)
            new_text = text[:start] + rewrap_armour(text[start:end]).rstrip("\n") + text[end:]
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, ValueError, VaultError) as e:
            print("[vault] couldn't update header in %s: %s" % (path.name, e))


def library_versions():
    """For the help page: the crypto library actually bundled."""
    out = {"cryptography": "", "openssl": ""}
    try:
        import cryptography
        out["cryptography"] = cryptography.__version__
    except Exception:
        pass
    try:
        from cryptography.hazmat.backends.openssl import backend
        out["openssl"] = backend.openssl_version_text()
    except Exception:
        pass
    return out
