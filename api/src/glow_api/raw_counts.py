"""Live, un-pinned per-period record counts.

Deliberately NOT vintage-pinned: this gives visibility into collection
progress between vintage cuts (so admin knows when it's worth cutting one,
and WRC/teachers can see progress without seeing suppressed values).

Because it's live, it must apply the same min_n floor as the main
suppression - otherwise it would be a sharper timing oracle than today's
/query already exposes, undermining the whole point of vintage-pinning the
actual suppressed output.
"""

from typing import Optional

import pandas as pd


def floored_period_counts(df: pd.DataFrame, min_n: int) -> dict[str, Optional[int]]:
    """Distinct-student count per period_id, floored at min_n.

    Distinct uid, not row count: matches /query's n (computed on a frame
    already deduped to one row per uid per period) and /export's suppression
    ("counts students, not rows") without depending on that dedup step
    having already happened upstream.
    """
    if df.empty or "period_id" not in df.columns or "uid" not in df.columns:
        return {}
    counts = df.dropna(subset=["period_id"]).groupby("period_id")["uid"].nunique()
    return {
        str(period): (int(n) if n >= min_n else None) for period, n in counts.items()
    }
