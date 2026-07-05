
from app.services.password_service import DUMMY_HASH, hash_password, verify_password


def test_hash_is_not_plaintext():
    assert hash_password("secret") != "secret"


def test_verify_correct_password():
    h = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", h) is True


def test_verify_wrong_password():
    h = hash_password("correct-horse-battery")
    assert verify_password("wrong-password", h) is False


def test_two_hashes_differ():
    """bcrypt salts each hash — same plaintext produces different hashes."""
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2


def test_dummy_hash_does_not_verify_arbitrary_input():
    """DUMMY_HASH must not accidentally accept common passwords."""
    assert verify_password("password", DUMMY_HASH) is False
    assert verify_password("", DUMMY_HASH) is False


def test_dummy_hash_is_valid_bcrypt():
    """DUMMY_HASH must be a valid hash string (not None or empty)."""
    assert DUMMY_HASH and DUMMY_HASH.startswith("$2b$")
