"""
bot/auth.py
Master authentication system.

Masters are defined in the DB with a bcrypt password hash and optional
host masks for auto-auth. Sessions are per (network, nick) and expire
on disconnect.

identify flow:
  /msg statsbot identify PeGaSuS p4$$w0rD
  → bot looks up PeGaSuS in masters table
  → verifies password
  → creates session for (network, current_nick, current_host)

auto-auth flow:
  WHO reply arrives with nick!user@host
  → bot checks if host matches any master mask
  → if yes, creates session without password
"""

import logging
import fnmatch
import time
from typing import Optional

log = logging.getLogger("auth")


class AuthManager:
    def __init__(self):
        # sessions: {(network, nick_lower): {"master": master_nick, "host": host, "ts": time}}
        self._sessions: dict = {}

    # ─── Session management ──────────────────────────────────────────────────

    def _session_key(self, network: str, nick: str) -> tuple:
        return (network.lower(), nick.lower())

    def create_session(self, network: str, nick: str, host: str, master_nick: str):
        key = self._session_key(network, nick)
        self._sessions[key] = {
            "master": master_nick,
            "host": host,
            "ts": time.time(),
        }
        log.info(f"Auth: {nick} ({host}) authenticated as {master_nick} on {network}")

    def destroy_session(self, network: str, nick: str):
        key = self._session_key(network, nick)
        if key in self._sessions:
            del self._sessions[key]
            log.info(f"Auth: session destroyed for {nick} on {network}")

    def get_session(self, network: str, nick: str) -> Optional[dict]:
        return self._sessions.get(self._session_key(network, nick))

    def is_authed(self, network: str, nick: str) -> bool:
        return self._session_key(network, nick) in self._sessions

    def on_nick_change(self, network: str, old_nick: str, new_nick: str):
        """Transfer session if nick changes."""
        old_key = self._session_key(network, old_nick)
        if old_key in self._sessions:
            session = self._sessions.pop(old_key)
            new_key = self._session_key(network, new_nick)
            self._sessions[new_key] = session
            log.info(f"Auth: session transferred {old_nick} → {new_nick} on {network}")

    def on_quit(self, network: str, nick: str):
        self.destroy_session(network, nick)

    def on_part(self, network: str, nick: str):
        # Don't expire on part — only on quit/disconnect
        pass

    # ─── Password verification ───────────────────────────────────────────────

    def identify(self, network: str, current_nick: str, host: str,
                  master_nick: str, password: str) -> tuple[bool, str]:
        """
        Attempt to authenticate current_nick as master_nick using password.
        Returns (success, message).
        """
        from database.models import get_master_by_nick
        master = get_master_by_nick(master_nick)
        if not master:
            log.warning(f"Auth: identify failed — unknown master {master_nick!r} from {current_nick} on {network}")
            return False, "Unknown master nick."

        if not _verify_password(password, master["password_hash"]):
            log.warning(f"Auth: identify failed — bad password for {master_nick!r} from {current_nick} on {network}")
            return False, "Wrong password."

        self.create_session(network, current_nick, host, master_nick)
        return True, f"Identified as {master_nick}."

    def try_auto_auth(self, network: str, nick: str, host: str) -> bool:
        """
        Check if nick!host matches any master mask and auto-auth if so.
        Returns True if auto-authed.
        """
        from database.models import list_masters_global
        full = f"{nick.lower()}!{host.lower()}"
        for master in list_masters_global():
            for mask in (master.get("masks") or "").split():
                if fnmatch.fnmatch(full, mask.lower()) or fnmatch.fnmatch(nick.lower(), mask.lower()):
                    if not self.is_authed(network, nick):
                        self.create_session(network, nick, host, master["nick"])
                        return True
        return False


# ─── Password hashing helpers ────────────────────────────────────────────────

def hash_password(plaintext: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    """Public alias for use in tests and external modules."""
    return _verify_password(plaintext, hashed)


def _verify_password(plaintext: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except Exception:
        return False
