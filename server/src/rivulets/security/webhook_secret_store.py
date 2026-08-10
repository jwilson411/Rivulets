"""Encrypts a WorkflowWebhook's HMAC signing secret at rest (#99), using
the same AES-GCM-with-workspace-derived-key approach as #118's
credential_fallback.py -- but stored directly on the WorkflowWebhook row
in rivulets.db rather than a separate file. That's safe here in a way it
isn't for provider keys (NFR-3.3, credential_fallback.py's docstring):
WorkflowWebhook is deliberately unsynced (see its own docstring, same
treatment as WorkflowSchedule), so the ciphertext never enters a sync
payload regardless of which table holds it.

Recoverable, not just hashed, because verifying an inbound HMAC signature
means recomputing it with the actual secret -- unlike Invite.secret_hash
(bcrypt, one-way equality check only), a webhook secret has to come back
out in full.
"""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12  # 96 bits, AES-GCM's recommended nonce size


def encrypt_webhook_secret(secret: str, key: bytes) -> tuple[bytes, bytes]:
    """Returns (nonce, ciphertext) to store as
    WorkflowWebhook.secret_nonce/secret_ciphertext."""
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), None)
    return nonce, ciphertext


def decrypt_webhook_secret(nonce: bytes, ciphertext: bytes, key: bytes) -> str:
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
