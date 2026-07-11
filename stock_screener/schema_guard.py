"""Defensive schema checks for parsed API responses.

The spec explicitly forbids guessing field names from training memory
without verification. Since this endpoint set has NOT been verified against
live responses yet (see docs/api_samples/README.md), every parser calls
`require_keys` on the first record so a real field-name mismatch fails
loudly with the actual keys seen — instead of silently producing rows full
of None, or crashing the whole pipeline with a raw KeyError.
"""

from __future__ import annotations


class SchemaMismatchError(Exception):
    def __init__(self, source: str, expected: set[str], actual: set[str]):
        missing = sorted(expected - actual)
        self.source = source
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"[{source}] 回應欄位與預期不符，缺少: {missing}。"
            f" 實際收到的欄位: {sorted(actual)}."
            f" 請執行 scripts/verify_api_samples.py 重新確認格式，"
            f" 並更新對應的 fetchers 模組。"
        )


def require_keys(record: dict, expected: set[str], source: str) -> None:
    actual = set(record.keys())
    if not expected.issubset(actual):
        raise SchemaMismatchError(source, expected, actual)
