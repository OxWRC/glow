#!/usr/bin/env python3
"""Slim the canonical glow-dummies base CSV down to a manageable seed size.

The full base dataset (~9,263 students across 20 schools) produces ~60,918
ODK submissions once transformed via transform_mock_data.py -- over 5 hours
to seed through ODK's throttled HTTP API. This keeps every school's distinct
test-scenario plan intact (transform_mock_data.py assigns each school a
unique target_waves/phq_mode/v1-quirk combination) while cutting each school
down to a small, deterministically-chosen subset of its classes -- entire
classes only, never partial -- landing the transformed dataset around ~12k
submissions.

The two donor schools (transform_mock_data.py's alphabetically-last two
school names, used to supply wave-4/5 "joiner" data for other schools) only
need >=1 retained student for that role (transform_mock_data.py:375-393
picks one via a hash modulo the donor pool size -- no other minimum).  This
script's floor of 1 kept class per school (~28+ students on this dataset)
clears that by a wide margin.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

SEED = 42
MAX_CLASSES_PER_SCHOOL = 6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # sorted() matches transform_mock_data.py's own school ordering exactly --
    # that ordering is what determines which two schools are donor-only, so
    # this must stay in lockstep with transform_mock_data.py:332.
    schools = sorted({row["school"] for row in rows})
    classes_by_school: dict[str, set[str]] = {}
    for row in rows:
        classes_by_school.setdefault(row["school"], set()).add(row["class"])

    rng = random.Random(SEED)
    kept_classes: dict[str, set[str]] = {}
    for school in schools:
        available = sorted(classes_by_school[school])
        n_keep = rng.randint(1, min(MAX_CLASSES_PER_SCHOOL, len(available)))
        kept_classes[school] = set(available[:n_keep])

    kept_rows = [row for row in rows if row["class"] in kept_classes[row["school"]]]

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    kept_students = len({row["uid"] for row in kept_rows})
    print(f"Kept {kept_students} students ({len(kept_rows)} wave-rows) across {len(schools)} schools")
    for school in schools:
        print(f"  {school}: {len(kept_classes[school])}/{len(classes_by_school[school])} classes")


if __name__ == "__main__":
    main()
