"""Key derivation hierarchy (security/keys.py, see its module docstring
and docs/security.md) -- the whole chain from a BIP-39 mnemonic down to
every HKDF-derived key this app uses (JWT signing, P2P PSK, mDNS
fingerprint, credential-store fallback, webhook secret) and the two
bcrypt-hashed secrets (workspace key, invite secret). No prior test file
exercised this module at all, despite it being the root of every other
security module's trust: a bug here (e.g. two different `info` strings
that happened to derive the same key, or a derivation that isn't
deterministic) would be effectively invisible everywhere else.
"""

from rivulets.security import keys

_SEED_A = b"\x11" * 64
_SEED_B = b"\x22" * 64


def test_generate_mnemonic_produces_a_valid_twelve_word_phrase() -> None:
    phrase = keys.generate_mnemonic()
    assert len(phrase.split()) == 12
    assert keys.is_valid_mnemonic(phrase)


def test_generate_mnemonic_is_not_deterministic() -> None:
    assert keys.generate_mnemonic() != keys.generate_mnemonic()


def test_is_valid_mnemonic_rejects_garbage() -> None:
    assert keys.is_valid_mnemonic("not a real bip39 phrase at all here") is False


def test_derive_seed_is_deterministic() -> None:
    phrase = keys.generate_mnemonic()
    assert keys.derive_seed(phrase) == keys.derive_seed(phrase)


def test_derive_seed_differs_with_passphrase() -> None:
    """The optional BIP-39 passphrase (module docstring) is applied before
    seed derivation -- the same mnemonic with vs. without one must produce
    different seeds, or the passphrase would be providing no security."""
    phrase = keys.generate_mnemonic()
    assert keys.derive_seed(phrase) != keys.derive_seed(phrase, "a passphrase")


def test_derive_seed_differs_between_mnemonics() -> None:
    first = keys.generate_mnemonic()
    second = keys.generate_mnemonic()
    assert keys.derive_seed(first) != keys.derive_seed(second)


def test_derive_workspace_key_is_deterministic_and_256_bits() -> None:
    key = keys.derive_workspace_key(_SEED_A)
    assert key == keys.derive_workspace_key(_SEED_A)
    assert len(key) == 32


def test_derive_workspace_key_differs_between_seeds() -> None:
    assert keys.derive_workspace_key(_SEED_A) != keys.derive_workspace_key(_SEED_B)


def test_every_derived_key_is_domain_separated() -> None:
    """The whole point of HKDF's `info` parameter (module docstring's
    hierarchy diagram): every key derived from the *same* workspace key
    must still be distinct from every other one, or compromising one
    purpose (e.g. a leaked JWT) would leak the P2P PSK or credential-store
    key too. Deterministic per input is equally load-bearing -- every node
    in a workspace has to independently re-derive the exact same keys
    from the exact same mnemonic (docs/security.md's key-derivation
    diagram) for sync/JWT verification to work at all."""
    workspace_key = keys.derive_workspace_key(_SEED_A)

    derived = {
        "agentos_security_key": keys.derive_agentos_security_key(workspace_key),
        "jwt_signing_key": keys.derive_jwt_signing_key(workspace_key),
        "p2p_psk": keys.derive_p2p_psk(workspace_key),
        "workspace_fingerprint": keys.derive_workspace_fingerprint(workspace_key),
        "credential_store_key": keys.derive_credential_store_key(workspace_key),
        "webhook_secret_key": keys.derive_webhook_secret_key(workspace_key),
    }

    # All pairwise distinct, including against the workspace key itself.
    values = [workspace_key, *derived.values()]
    assert len(set(values)) == len(values), f"key collision among: {derived.keys()}"

    # Each one is independently deterministic given the same workspace key.
    assert keys.derive_agentos_security_key(workspace_key) == derived["agentos_security_key"]
    assert keys.derive_jwt_signing_key(workspace_key) == derived["jwt_signing_key"]
    assert keys.derive_p2p_psk(workspace_key) == derived["p2p_psk"]
    assert keys.derive_workspace_fingerprint(workspace_key) == derived["workspace_fingerprint"]
    assert keys.derive_credential_store_key(workspace_key) == derived["credential_store_key"]
    assert keys.derive_webhook_secret_key(workspace_key) == derived["webhook_secret_key"]


def test_derive_workspace_fingerprint_matches_across_two_logins_with_the_same_mnemonic() -> None:
    """The exact scenario derive_workspace_fingerprint's docstring exists
    for: two different nodes logging into the same workspace with the
    same mnemonic must derive the same fingerprint, independent of any
    per-node random state (unlike Workspace.id)."""
    phrase = keys.generate_mnemonic()
    node_a_key = keys.derive_workspace_key(keys.derive_seed(phrase))
    node_b_key = keys.derive_workspace_key(keys.derive_seed(phrase))
    assert keys.derive_workspace_fingerprint(node_a_key) == keys.derive_workspace_fingerprint(
        node_b_key
    )


def test_hash_workspace_key_round_trips_via_verify() -> None:
    workspace_key = keys.derive_workspace_key(_SEED_A)
    key_hash = keys.hash_workspace_key(workspace_key)
    assert keys.verify_workspace_key(workspace_key, key_hash) is True


def test_verify_workspace_key_rejects_the_wrong_key() -> None:
    key_hash = keys.hash_workspace_key(keys.derive_workspace_key(_SEED_A))
    assert keys.verify_workspace_key(keys.derive_workspace_key(_SEED_B), key_hash) is False


def test_hash_workspace_key_salts_each_call_differently() -> None:
    """bcrypt.gensalt() draws a fresh salt every call -- hashing the same
    key twice must not produce identical hashes, or two workspaces (or two
    logins) sharing a mnemonic would be trivially linkable from the stored
    hash alone."""
    workspace_key = keys.derive_workspace_key(_SEED_A)
    assert keys.hash_workspace_key(workspace_key) != keys.hash_workspace_key(workspace_key)


def test_generate_invite_secret_is_random_and_url_safe() -> None:
    first = keys.generate_invite_secret()
    second = keys.generate_invite_secret()
    assert first != second
    assert len(first) > 32


def test_hash_and_verify_invite_secret_round_trip() -> None:
    secret = keys.generate_invite_secret()
    secret_hash = keys.hash_invite_secret(secret)
    assert keys.verify_invite_secret(secret, secret_hash) is True


def test_verify_invite_secret_rejects_the_wrong_secret() -> None:
    secret_hash = keys.hash_invite_secret(keys.generate_invite_secret())
    assert keys.verify_invite_secret("wrong-secret", secret_hash) is False


def test_generate_webhook_secret_is_random() -> None:
    first = keys.generate_webhook_secret()
    second = keys.generate_webhook_secret()
    assert first != second
    assert len(first) > 32
