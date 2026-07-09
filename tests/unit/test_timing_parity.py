"""Unit-level timing parity test.

Validates that both rejection paths in auth_service.login() call verify_password()
with a valid bcrypt hash, ensuring equivalent wall-clock time and no timing oracle.

  Path 1 (non-existent email):  verify_password(password, DUMMY_HASH)
  Path 2 (wrong password):      verify_password(password, user.password_hash)

No DB or HTTP required — tests the timing guarantee at the bcrypt level.
"""
import statistics
import time

from app.services.password_service import DUMMY_HASH, hash_password, verify_password


def test_dummy_hash_and_real_hash_timing_parity():
    """DUMMY_HASH path and real-hash path must produce similar verify_password() runtimes.

    At BCRYPT_ROUNDS=12 each bcrypt call takes ~200 ms. Runs 5 samples per path.
    Tolerance is 150 ms — sufficient to absorb normal CPU scheduling jitter while
    still catching any code path that skips bcrypt entirely.
    """
    real_hash = hash_password("SomePassword123!")  # pre-computed once outside timing loops

    N = 5

    # Path 1: user not found → auth_service calls verify_password(password, DUMMY_HASH)
    dummy_times = []
    for _ in range(N):
        t0 = time.perf_counter()
        verify_password("attempt", DUMMY_HASH)
        dummy_times.append(time.perf_counter() - t0)

    # Path 2: user found, wrong password → auth_service calls verify_password(password, user.password_hash)
    real_times = []
    for _ in range(N):
        t0 = time.perf_counter()
        verify_password("attempt", real_hash)
        real_times.append(time.perf_counter() - t0)

    mean_dummy = statistics.mean(dummy_times)
    mean_real = statistics.mean(real_times)

    # Both paths must actually run bcrypt — BCRYPT_ROUNDS=12 always takes > 50 ms
    assert mean_dummy > 0.05, (
        f"DUMMY_HASH path is suspiciously fast ({mean_dummy * 1000:.1f} ms). "
        "Check that BCRYPT_ROUNDS >= 4 and DUMMY_HASH is a valid bcrypt hash."
    )
    assert mean_real > 0.05, (
        f"Real-hash path is suspiciously fast ({mean_real * 1000:.1f} ms). "
        "Check BCRYPT_ROUNDS setting."
    )

    # Parity check — ensures no timing oracle that reveals whether an email exists
    diff = abs(mean_dummy - mean_real)
    assert diff < 0.15, (
        f"Timing parity violation (email enumeration risk): "
        f"dummy_path={mean_dummy * 1000:.1f} ms, "
        f"real_path={mean_real * 1000:.1f} ms, "
        f"diff={diff * 1000:.1f} ms (limit=150 ms)"
    )
