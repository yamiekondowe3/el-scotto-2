"""Tests for the forward-test monitor.

The first test is the important one: it asserts the read-only invariant by
scanning source, not by mocking. A mock proves the code under test behaves; a
source scan proves nobody added an order call anywhere in the package.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def test_no_order_placement_anywhere_in_python():
    """All order placement lives in the EA. If this fails, a Python bug can
    cost money -- which is the whole reason the boundary exists."""
    import re
    # Match an actual call -- attribute access followed by a paren -- not the
    # words appearing in prose. A plain substring scan flags every docstring
    # that discusses the invariant, which is a false positive rather than a
    # finding. order_check() is explicitly fine: it validates without executing.
    #
    # Note this comment deliberately avoids writing the call form literally;
    # an earlier version spelled it out inside backticks and tripped its own
    # test. Scope is this repository only -- scanning a parent workspace picks
    # up unrelated sibling projects.
    call = re.compile(r"\.order" + r"_send\s*\(")
    offenders = []
    for p in ROOT.rglob("*.py"):
        if any(part in {"venv", "__pycache__", "el-scotto-review"} for part in p.parts):
            continue
        if p.name == Path(__file__).name:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if call.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"order placement found in Python: {offenders}"


def test_entry_atr_parsed_from_ea_comment():
    from monitor import _entry_atr_from_comment
    assert _entry_atr_from_comment("els atr=12.34567") == pytest.approx(12.34567)
    assert _entry_atr_from_comment("els atr=0.5 extra") == pytest.approx(0.5)
    assert _entry_atr_from_comment("no atr here") is None
    assert _entry_atr_from_comment("") is None
    # must not raise on malformed input from a broker-mangled comment
    assert _entry_atr_from_comment("els atr=") is None
    assert _entry_atr_from_comment("els atr=abc") is None


def test_append_jsonl_is_idempotent(tmp_path):
    from monitor import append_jsonl
    f = tmp_path / "t.jsonl"
    rows = [{"position_id": 1, "v": "a"}, {"position_id": 2, "v": "b"}]
    assert append_jsonl(f, rows, "position_id") == 2
    # polling again must add nothing: the monitor runs on a timer and would
    # otherwise duplicate every trade on every poll
    assert append_jsonl(f, rows, "position_id") == 0
    assert append_jsonl(f, rows + [{"position_id": 3, "v": "c"}], "position_id") == 1
    lines = [json.loads(l) for l in f.open() if l.strip()]
    assert [r["position_id"] for r in lines] == [1, 2, 3]


def test_append_jsonl_survives_a_corrupt_line(tmp_path):
    from monitor import append_jsonl
    f = tmp_path / "t.jsonl"
    f.write_text('{"position_id": 1}\nnot json at all\n')
    # a truncated write from a killed process must not block future appends
    assert append_jsonl(f, [{"position_id": 1}, {"position_id": 9}], "position_id") == 1


def test_expectation_constants_match_the_report():
    """The pre-registered numbers are locked. If someone edits them to match a
    disappointing forward result, that is moving the goalposts, and this test
    is the tripwire."""
    import monitor
    assert monitor.EXPECTED_ER == 0.0873
    assert monitor.EXPECTED_TRADES_PER_YEAR == 40
    assert monitor.MAGIC == 20260908
