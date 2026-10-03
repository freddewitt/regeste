"""API keys live in the OS keychain (macOS Keychain, Windows Credential Manager,
Secret Service), never in `regeste.json`.

One entry per provider identity (`kind` + `base_url`), shared by every project:
a key is typed once. If no keychain is available, nothing is stored and the
key must be typed again next time (it is never written in clear).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SERVICE = "regeste"


def _account(kind: str, base_url: str | None) -> str:
    return f"{kind}|{(base_url or '').rstrip('/')}"


def store_api_key(kind: str, base_url: str | None, api_key: str | None) -> bool:
    """Save `api_key` in the keychain. Returns True if it is safely stored."""
    if not api_key:
        return False
    try:
        import keyring

        keyring.set_password(SERVICE, _account(kind, base_url), api_key)
        return True
    except Exception as exc:  # noqa: BLE001 - no usable keychain: degrade, never crash
        logger.warning("Keychain unavailable, API key not saved (%s)", exc)
        return False


def load_api_key(kind: str, base_url: str | None) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, _account(kind, base_url)) or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Keychain unavailable, API key not loaded (%s)", exc)
        return None
