"""Webhook secret at-rest encryption (security/webhook_secret_store.py,
#99) -- an inbound webhook's HMAC signing secret has to be recoverable in
full to verify a request's signature, unlike Invite.secret_hash's
one-way bcrypt comparison, so this is AES-GCM encrypt/decrypt rather than
a hash. No prior test file exercised this module at all.
"""

import pytest
from cryptography.exceptions import InvalidTag

from rivulets.security.webhook_secret_store import decrypt_webhook_secret, encrypt_webhook_secret

_KEY_A = b"\x01" * 32
_KEY_B = b"\x02" * 32


def test_encrypt_then_decrypt_round_trips() -> None:
    nonce, ciphertext = encrypt_webhook_secret("whsec-real-secret", _KEY_A)
    assert decrypt_webhook_secret(nonce, ciphertext, _KEY_A) == "whsec-real-secret"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    _nonce, ciphertext = encrypt_webhook_secret("whsec-real-secret", _KEY_A)
    assert b"whsec-real-secret" not in ciphertext


def test_nonce_is_random_per_call() -> None:
    """A reused nonce under the same key breaks AES-GCM's confidentiality
    guarantee -- each encryption must draw a fresh one (os.urandom, not a
    counter or fixed value)."""
    nonce_one, _ct_one = encrypt_webhook_secret("whsec-a", _KEY_A)
    nonce_two, _ct_two = encrypt_webhook_secret("whsec-a", _KEY_A)
    assert nonce_one != nonce_two


def test_wrong_key_fails_to_decrypt() -> None:
    """AES-GCM authenticates the ciphertext -- a wrong key must raise
    rather than silently return garbage, since a corrupted decrypt here
    would otherwise surface as a bogus HMAC secret used to (incorrectly)
    verify an inbound webhook's signature."""
    nonce, ciphertext = encrypt_webhook_secret("whsec-real-secret", _KEY_A)
    with pytest.raises(InvalidTag):
        decrypt_webhook_secret(nonce, ciphertext, _KEY_B)


def test_tampered_ciphertext_fails_to_decrypt() -> None:
    """The AEAD authentication tag must catch a flipped byte, not just a
    wrong key -- otherwise a corrupted/tampered row could decrypt to
    silently wrong bytes instead of raising."""
    nonce, ciphertext = encrypt_webhook_secret("whsec-real-secret", _KEY_A)
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(InvalidTag):
        decrypt_webhook_secret(nonce, tampered, _KEY_A)
