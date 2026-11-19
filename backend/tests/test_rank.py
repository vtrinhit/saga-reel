"""W0: fractional index base-62 (`core/rank.py`)."""

from __future__ import annotations

import random

import pytest

from sagareel.core import rank


def test_first_key():
    assert rank.between(None, None) == "a0"


def test_basic_order():
    a = rank.between(None, None)
    b = rank.between(a, None)
    z = rank.between(None, a)
    m = rank.between(a, b)
    assert z < a < m < b


def test_rejects_bad_order():
    with pytest.raises(ValueError):
        rank.between("a1", "a0")
    with pytest.raises(ValueError):
        rank.between("a0", "a0")


def test_n_after_increasing_and_short():
    keys = rank.n_after(None, 1000)
    assert keys == sorted(keys) and len(set(keys)) == 1000
    assert keys[0] == "a0"
    assert max(len(k) for k in keys) <= 3
    more = rank.n_after(keys[-1], 5)
    assert keys[-1] < more[0] and more == sorted(more)


def test_alphabet_is_ascii_ordered():
    # Thứ tự Python == thứ tự BINARY của SQLite khi mọi ký tự là ASCII tăng dần.
    assert list(rank.DIGITS) == sorted(rank.DIGITS) and len(rank.DIGITS) == 62


def test_random_inserts_keep_order():
    rnd = random.Random(1234)
    keys: list[str] = []
    for _ in range(10_000):
        i = rnd.randint(0, len(keys))
        lo = keys[i - 1] if i > 0 else None
        hi = keys[i] if i < len(keys) else None
        k = rank.between(lo, hi)
        assert (lo is None or lo < k) and (hi is None or k < hi)
        keys.insert(i, k)
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_middle_inserts_stay_short():
    rnd = random.Random(42)
    keys = rank.n_after(None, 10)
    for _ in range(1000):
        i = rnd.randint(1, len(keys) - 1)
        keys.insert(i, rank.between(keys[i - 1], keys[i]))
    assert keys == sorted(keys)
    assert max(len(k) for k in keys) <= 12


def test_same_gap_inserts_then_rebalance():
    a, b = rank.n_after(None, 2)
    keys = [a, b]
    for _ in range(300):  # luôn chèn sát sau phần tử đầu → khoá dài dần
        keys.insert(1, rank.between(keys[0], keys[1]))
    assert keys == sorted(keys)
    fresh = rank.rebalance(keys)
    assert len(fresh) == len(keys) and fresh == sorted(fresh) and len(set(fresh)) == len(fresh)
    assert max(len(k) for k in fresh) <= 3


def test_prepend_many():
    k = "a0"
    for _ in range(500):
        nk = rank.between(None, k)
        assert nk < k
        k = nk
