"""Regression test for find_by_id_prefix.

We're not testing Firestore itself — just locking in that the function's
range-query construction uses the proper prefix-match idiom. A future "cleanup"
that removes the `\\uf8ff` would silently break /retry and /delete.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_find_by_id_prefix_uses_high_unicode_upper_bound(monkeypatch):
    """The Firestore prefix-match idiom requires `prefix + \\uf8ff` as the upper bound.

    Catches the class of bug where someone "simplifies" `end = prefix + "\\uf8ff"`
    to `end = prefix + ""` — which produces an empty range and a silent no-op.
    """
    from app import db

    captured_filters: list = []

    class FakeQuery:
        def where(self, filter):
            captured_filters.append(filter)
            return self

        def limit(self, n):
            return self

        async def stream(self):
            # No matches — empty async iterator
            if False:
                yield
            return

    class FakeCollection:
        def where(self, filter):
            captured_filters.append(filter)
            return FakeQuery()

        def document(self, doc_id):
            # Return an object that captures the path used for the bound
            mock = MagicMock()
            mock.id = doc_id
            mock._path = doc_id
            return mock

    monkeypatch.setattr(db, "_col", lambda: FakeCollection())

    rows = await db.find_by_id_prefix("abc123")
    assert rows == []

    # Expect 2 filters: __name__ >= doc(prefix), __name__ < doc(prefix + )
    assert len(captured_filters) == 2
    upper_bound_ref = captured_filters[1].value
    # The upper bound's document id should contain the high unicode sentinel —
    # NOT just the bare prefix.
    assert "abc123" in upper_bound_ref._path
    assert "" in upper_bound_ref._path, (
        "find_by_id_prefix dropped the \\uf8ff sentinel — prefix queries will return empty"
    )


@pytest.mark.asyncio
async def test_find_by_id_prefix_empty_prefix_short_circuits(monkeypatch):
    from app import db

    # Should not even build a query for empty prefix
    called = []

    def fake_col():
        called.append(True)
        return MagicMock()

    monkeypatch.setattr(db, "_col", fake_col)
    rows = await db.find_by_id_prefix("")
    assert rows == []
    assert called == []
