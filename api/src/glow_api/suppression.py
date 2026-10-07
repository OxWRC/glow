"""Coarsening-hierarchy suppression for the pseudonymous data export.

Python port of glow-suppressor's engine (config parsing and label chains),
plus the escalation loop that engine left out. Config format:
docs/superpowers/specs/2026-09-07-coarsening-hierarchy-suppression-design.md.

Matching is case-insensitive and whole-value (re.fullmatch), for both
`input` and `input_regex`.
"""

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

RULES_PATH = Path(__file__).parent / "suppression.yaml"


class SuppressionConfigError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Rule:
    output: str
    literal: str | None = None
    pattern: re.Pattern | None = None

    def matches(self, value: str) -> bool:
        if self.literal is not None:
            return self.literal.lower() == value.lower()
        return self.pattern.fullmatch(value) is not None


@dataclass(frozen=True)
class Rules:
    dimensions: list[str]
    hierarchies: dict[str, list[list[Rule]]]
    escalation: list[str]
    min_n: int
    sha256: str


def _parse_level(dim: str, index: int, level, errors: list[str]) -> list[Rule]:
    rules_raw = level.get("map") if isinstance(level, dict) else None
    if not isinstance(rules_raw, list):
        errors.append(f"hierarchies.{dim}[{index}].map must be a list")
        return []
    rules = []
    for rule_index, raw in enumerate(rules_raw):
        where = f"hierarchies.{dim}[{index}].map[{rule_index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where} must be a mapping")
            continue
        has_input = isinstance(raw.get("input"), str)
        has_regex = isinstance(raw.get("input_regex"), str)
        if has_input == has_regex:
            errors.append(f"{where} must have exactly one of input or input_regex")
            continue
        if not isinstance(raw.get("output"), str):
            errors.append(f"{where} must have a string output")
            continue
        if has_input:
            rules.append(Rule(output=raw["output"], literal=raw["input"]))
            continue
        try:
            pattern = re.compile(raw["input_regex"], re.IGNORECASE)
        except re.error as exc:
            errors.append(f"{where} input_regex does not compile: {exc}")
            continue
        rules.append(Rule(output=raw["output"], pattern=pattern))
    return rules


def parse_rules(text: str) -> Rules:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SuppressionConfigError([f"invalid YAML: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise SuppressionConfigError(
            ["config must be a mapping with dimensions, hierarchies, escalation"]
        )
    errors: list[str] = []

    min_n = raw.get("min_n")
    if not (isinstance(min_n, int) and not isinstance(min_n, bool) and min_n >= 1):
        errors.append("min_n must be a positive integer")

    dimensions = raw.get("dimensions")
    if not (
        isinstance(dimensions, list)
        and dimensions
        and all(isinstance(d, str) for d in dimensions)
    ):
        errors.append("dimensions must be a non-empty list of strings")
        dimensions = []

    hierarchies: dict[str, list[list[Rule]]] = {}
    raw_hierarchies = raw.get("hierarchies") or {}
    if not isinstance(raw_hierarchies, dict):
        errors.append("hierarchies must be a mapping of dimension name to level list")
        raw_hierarchies = {}
    for dim, levels in raw_hierarchies.items():
        if not isinstance(levels, list):
            errors.append(f"hierarchies.{dim} must be a list of levels")
            continue
        hierarchies[dim] = [
            _parse_level(dim, i, level, errors) for i, level in enumerate(levels)
        ]

    escalation = raw.get("escalation")
    if not (
        isinstance(escalation, list) and all(isinstance(e, str) for e in escalation)
    ):
        errors.append("escalation must be a list of strings")
        escalation = []
    for name in escalation:
        if name not in dimensions:
            errors.append(f'escalation entry "{name}" is not listed in dimensions')
    for name in dict.fromkeys(escalation):
        count, available = escalation.count(name), len(hierarchies.get(name, []))
        if count > available:
            errors.append(
                f'escalation uses "{name}" {count} times but hierarchies.{name} '
                f"only defines {available} level(s)"
            )

    if errors:
        raise SuppressionConfigError(errors)
    return Rules(
        dimensions=dimensions,
        hierarchies=hierarchies,
        escalation=escalation,
        min_n=min_n,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def load_rules(path: Path = RULES_PATH) -> Rules:
    return parse_rules(path.read_text())


def label(value) -> str:
    """Stringify a raw cell for matching; missing values become "NA"."""
    return "NA" if pd.isna(value) else str(value)


def coarsen_value(value: str, levels: list[list[Rule]], n: int) -> str:
    """Walk levels 1..n; first matching rule per level wins, else passthrough."""
    for level in levels[:n]:
        value = next((rule.output for rule in level if rule.matches(value)), value)
    return value


def _coarsen(series: pd.Series, levels: list[list[Rule]], n: int) -> pd.Series:
    mapping = {value: coarsen_value(value, levels, n) for value in series.unique()}
    return series.map(mapping)


def _smallest_group(current: dict[str, pd.Series], ids: pd.Series) -> float:
    """Distinct students in the smallest non-empty combination of dimensions."""
    if ids.empty:
        return math.inf
    if not current:
        return ids.nunique()
    frame = pd.DataFrame({**current, "_id": ids})
    return frame.groupby(list(current), dropna=False)["_id"].nunique().min()


def suppress(
    df: pd.DataFrame, rules: Rules, min_n: int, id_column: str = "uid"
) -> tuple[pd.DataFrame | None, dict[str, int]]:
    """Coarsen dimensions along `escalation` until every non-empty combination
    covers at least min_n distinct students.

    Returns (frame with dimension columns replaced by their coarsened labels,
    level per dimension), or (None, levels) when escalation runs out first,
    meaning the whole release is suppressed. Dimensions absent from df are
    ignored; escalation steps for them are consumed without effect.
    """
    dims = [d for d in rules.dimensions if d in df.columns]
    raw = {d: df[d].map(label) for d in dims}
    levels = {d: 0 for d in dims}
    pending = iter(rules.escalation)
    while True:
        current = {
            d: _coarsen(raw[d], rules.hierarchies.get(d, []), levels[d]) for d in dims
        }
        if _smallest_group(current, df[id_column]) >= min_n:
            out = df.copy()
            for d in dims:
                out[d] = current[d]
            return out, levels
        step = next(pending, None)
        if step is None:
            return None, levels
        if step in levels:
            levels[step] += 1
