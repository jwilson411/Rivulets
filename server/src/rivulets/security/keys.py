"""Key derivation hierarchy (see docs/security.md).

    BIP-39 Mnemonic (12 words)
      -> PBKDF2 (2048 rounds, built into Mnemonic.to_seed) -> BIP-39 Seed (512 bits)
           -> HKDF(info="workspace-key") -> Workspace Key (256 bits)
                -> bcrypt -> stored hash (the only persistent derivative)
                -> HKDF(info="agentos-auth") -> AgentOS Security Key
                -> HKDF(info="jwt-signing")   -> JWT Signing Key (HS256)
                -> HKDF(info="p2p-psk")       -> libp2p PSK
                -> HKDF(info="workspace-fingerprint") -> mDNS scoping tag
           -> optional BIP-39 passphrase applied before seed derivation

The mDNS fingerprint deliberately isn't the `Workspace.id` DB row's UUID:
that's generated fresh (uuid7, random) by whichever node happens to
bootstrap it first (api/auth.py) — two nodes logging into the *same*
workspace with the *same* mnemonic would each mint their own random
`Workspace.id`, so peers would never derive the same mDNS service type
and would never find each other (sync/discovery.py). The fingerprint has
to come from the one thing every node in the workspace actually shares:
the workspace key itself.

Every derived key is computed at login time and lives in memory only —
nothing here writes a raw or derived key to disk. Callers own that
discipline; this module just does the math.
"""

import bcrypt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from mnemonic import Mnemonic

_WORKSPACE_KEY_LENGTH = 32  # 256 bits
_MNEMONIC_STRENGTH_BITS = 128  # -> 12 words

_mnemonic = Mnemonic("english")


def generate_mnemonic() -> str:
    """Generate a new 12-word BIP-39 recovery phrase (FR-1.2, OQ-7)."""
    return _mnemonic.generate(strength=_MNEMONIC_STRENGTH_BITS)


def is_valid_mnemonic(phrase: str) -> bool:
    return _mnemonic.check(phrase)


def derive_seed(mnemonic_phrase: str, passphrase: str = "") -> bytes:
    """BIP-39 seed: PBKDF2-HMAC-SHA512, 2048 rounds, 64 bytes."""
    return _mnemonic.to_seed(mnemonic_phrase, passphrase)


def _hkdf(seed: bytes, info: str, length: int = _WORKSPACE_KEY_LENGTH) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info.encode("utf-8"),
    ).derive(seed)


def derive_workspace_key(seed: bytes) -> bytes:
    return _hkdf(seed, "workspace-key")


def derive_agentos_security_key(workspace_key: bytes) -> bytes:
    return _hkdf(workspace_key, "agentos-auth")


def derive_jwt_signing_key(workspace_key: bytes) -> bytes:
    return _hkdf(workspace_key, "jwt-signing")


def derive_p2p_psk(workspace_key: bytes) -> bytes:
    return _hkdf(workspace_key, "p2p-psk")


def derive_workspace_fingerprint(workspace_key: bytes) -> bytes:
    """A shared, stable identifier for mDNS discovery scoping (FR-9.3) —
    every node in the same workspace derives the same value from the same
    mnemonic, unlike `Workspace.id` (see module docstring)."""
    return _hkdf(workspace_key, "workspace-fingerprint")


def hash_workspace_key(workspace_key: bytes) -> str:
    """bcrypt hash — the only persistent derivative of the workspace key
    (NFR-3.1). Stored as `workspace.key_hash`."""
    return bcrypt.hashpw(workspace_key, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_workspace_key(workspace_key: bytes, key_hash: str) -> bool:
    return bcrypt.checkpw(workspace_key, key_hash.encode("utf-8"))
