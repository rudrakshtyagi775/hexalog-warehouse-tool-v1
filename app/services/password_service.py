import bcrypt

from app.config import settings

# Pre-hashed placeholder — always run verify_password() for non-existent users
# so the endpoint takes the same wall-clock time whether or not the email exists.
DUMMY_HASH = bcrypt.hashpw(
    b"__dummy_password_for_timing_parity__",
    bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS),
).decode()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(
        plain.encode(), bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    ).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())
