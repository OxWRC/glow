"""Tests for the coarsening-hierarchy suppression engine."""

import pandas as pd
import pytest

from glow_api.suppression import (
    SuppressionConfigError,
    coarsen_value,
    load_rules,
    parse_rules,
    suppress,
)

ETHNICITY_RULES = """
min_n: 2
dimensions: [school, ethnicity]
hierarchies:
  ethnicity:
    - map:
        - {input: "White British", output: White}
        - {input_regex: "White.*", output: White}
        - {input: "Indian", output: Asian}
    - map:
        - {input_regex: ".*", output: "*"}
escalation: [ethnicity, ethnicity]
"""


def _rules(text=ETHNICITY_RULES):
    return parse_rules(text)


def test_literal_match_is_case_insensitive():
    levels = _rules().hierarchies["ethnicity"]
    assert coarsen_value("white british", levels, 1) == "White"


def test_regex_match_is_whole_value():
    rules = parse_rules(
        """
min_n: 1
dimensions: [x]
hierarchies:
  x:
    - map:
        - {input_regex: "White", output: W}
escalation: [x]
"""
    )
    levels = rules.hierarchies["x"]
    assert coarsen_value("WHITE", levels, 1) == "W"
    assert coarsen_value("White Irish", levels, 1) == "White Irish"


def test_unmatched_value_passes_through():
    levels = _rules().hierarchies["ethnicity"]
    assert coarsen_value("Martian", levels, 1) == "Martian"


def test_levels_chain_off_previous_output():
    levels = _rules().hierarchies["ethnicity"]
    assert coarsen_value("White Irish", levels, 0) == "White Irish"
    assert coarsen_value("White Irish", levels, 1) == "White"
    assert coarsen_value("White Irish", levels, 2) == "*"


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("dimensions: []\nescalation: []", "dimensions must be a non-empty list"),
        ("dimensions: [a]\nescalation: [b]", '"b" is not listed in dimensions'),
        ("dimensions: [a]\nescalation: [a]", "only defines 0 level(s)"),
        (
            "dimensions: [a]\nhierarchies:\n  a:\n    - map:\n        - {input_regex: '(', output: x}\nescalation: []",
            "does not compile",
        ),
        (
            "dimensions: [a]\nhierarchies:\n  a:\n    - map:\n        - {input: x, input_regex: y, output: z}\nescalation: []",
            "exactly one of input or input_regex",
        ),
        ("- just a list", "must be a mapping"),
        (
            "min_n: 0\ndimensions: [a]\nescalation: []",
            "min_n must be a positive integer",
        ),
        ("dimensions: [a]\nescalation: []", "min_n must be a positive integer"),
    ],
)
def test_invalid_config_raises_with_message(text, fragment):
    with pytest.raises(SuppressionConfigError) as exc:
        parse_rules(text)
    assert any(fragment in e for e in exc.value.errors)


def test_sha256_is_of_raw_text():
    import hashlib

    assert _rules().sha256 == hashlib.sha256(ETHNICITY_RULES.encode()).hexdigest()


def _frame(rows):
    return pd.DataFrame(rows, columns=["uid", "school", "ethnicity"])


def test_passes_at_level_zero_when_groups_big_enough():
    df = _frame([(f"S{i}", "A", "Indian") for i in range(3)])
    out, coarsening = suppress(df, _rules(), min_n=3)
    assert coarsening == {"school": 0, "ethnicity": 0}
    assert list(out["ethnicity"]) == ["Indian"] * 3


def test_escalates_until_groups_clear_threshold():
    df = _frame(
        [
            ("S1", "A", "White British"),
            ("S2", "A", "White Irish"),
            ("S3", "A", "Indian"),
        ]
    )
    out, coarsening = suppress(df, _rules(), min_n=3)
    assert coarsening == {"school": 0, "ethnicity": 2}
    assert set(out["ethnicity"]) == {"*"}


def test_returns_none_when_escalation_exhausted():
    df = _frame([("S1", "A", "Indian"), ("S2", "B", "Indian")])
    out, coarsening = suppress(df, _rules(), min_n=2)
    assert out is None
    assert coarsening == {"school": 0, "ethnicity": 2}


def test_counts_students_not_rows():
    # One student over three periods is still a group of one.
    df = _frame([("S1", "A", "Indian")] * 3)
    out, _ = suppress(df, _rules(), min_n=2)
    assert out is None


def test_na_values_form_their_own_group():
    df = _frame([("S1", "A", None), ("S2", "A", None), ("S3", "A", "Indian")])
    df2 = _frame([("S1", "A", None), ("S2", "A", None)])
    _, coarsening = suppress(df, _rules(), min_n=2)
    assert coarsening["ethnicity"] == 2  # "Indian" alone forced escalation
    out, coarsening = suppress(df2, _rules(), min_n=2)
    assert coarsening["ethnicity"] == 0
    assert list(out["ethnicity"]) == ["NA", "NA"]


def test_dimensions_absent_from_frame_are_ignored():
    df = pd.DataFrame({"uid": ["S1", "S2"], "school": ["A", "A"]})
    out, coarsening = suppress(df, _rules(), min_n=2)
    assert coarsening == {"school": 0}
    assert out is not None


def test_empty_frame_passes():
    out, _ = suppress(_frame([]), _rules(), min_n=5)
    assert out is not None and out.empty


def test_shipped_rules_file_loads():
    rules = load_rules()
    assert rules.min_n == 5
    assert "school" in rules.dimensions
    assert "class" in rules.dimensions
    assert "d_genderIdentity" in rules.dimensions
    assert "d_sexualOrientation" in rules.dimensions


PERIOD_RULES = parse_rules(
    """
min_n: 5
dimensions: [school, d_sex]
hierarchies:
  school: [{map: [{input_regex: ".*", output: "*"}]}]
  d_sex: [{map: [{input_regex: ".*", output: "*"}]}]
escalation: [d_sex, school]
"""
)


def _period_frame(p2_students):
    rows = [(f"S{i}", "A", "F", "P1") for i in range(5)]
    rows += [(f"S{i}", "A", "F", "P2") for i in p2_students]
    return pd.DataFrame(rows, columns=["uid", "school", "d_sex", "period_id"])


def test_per_period_cells_must_clear_min_n():
    # 5 girls at A in P1, but only S0 in P2: the (A, *, P2) cell is 1 student.
    out, coarsening = suppress(_period_frame([0]), PERIOD_RULES, min_n=5)
    assert out is None
    assert "period_id" not in coarsening


def test_period_is_grouped_but_never_coarsened():
    out, coarsening = suppress(_period_frame(range(5)), PERIOD_RULES, min_n=5)
    assert coarsening == {"school": 0, "d_sex": 0}
    assert list(out["period_id"]) == ["P1"] * 5 + ["P2"] * 5
