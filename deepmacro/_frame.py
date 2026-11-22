"""DataFrame 组装工具 — {月份: {科目: 值}} → 宽表。"""
from __future__ import annotations

from typing import Any

import pandas as pd


def to_wide_frame(by_month: dict[str, dict[str, Any]],
                  index_name: str = '月份') -> pd.DataFrame:
    """{month: {subject: value}} → 宽表 DataFrame。

    行按月份升序；列按科目首次出现顺序排列（保持官方科目体系的自然顺序）。
    """
    months = sorted(by_month)
    subjects: list[str] = []
    seen: set[str] = set()
    for m in months:
        for s in by_month[m]:
            if s not in seen:
                seen.add(s)
                subjects.append(s)
    df = pd.DataFrame({
        index_name: months,
        **{s: [by_month[m].get(s) for m in months] for s in subjects},
    })
    return df


def clip_months(df: pd.DataFrame, start: str | None, end: str | None,
                 index_name: str = '月份') -> pd.DataFrame:
    """按 'YYYY-MM' 区间过滤宽表首列。"""
    if start is not None:
        df = df[df[index_name] >= start]
    if end is not None:
        df = df[df[index_name] <= end]
    return df.reset_index(drop=True)
