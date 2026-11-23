"""KOSIS 导出 CSV 解析器 + 指标取数（移植自 factorlab crawler_kr_official）。

CSV 形态（2026-11 探测冻结）：宽表，列 = 月份 'M202605 2026.05'（暂定月带
' p)' 后缀）、缺失 '-'；科目名可含逗号 → csv 模块解析（禁止 split(',')）。
kr_ppi 科目名尾随基期后缀 '총지수 (14999301.814 2020=100)' → 剥离。
kr_iip 表头 2 行（月份行 + T10/T20 项目行）。

deepmacro 适配：科目列用净名（无 code| 前缀）。
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from datetime import datetime

from deepmacro.korea._kosis import KosisSession, _proxy_url

_log = logging.getLogger(__name__)

KOSIS_CPI_ORG = '101'
KOSIS_CPI_TBL = 'DT_1J22001'      # 소비자물가지수（지출목적별）
KOSIS_CPI_BASE_PERIOD = '2020=100'
KR_CPI_EARLIEST = '2013-01'       # 全史 1965 起但旧基期链不连贯，取 10 年深史

KOSIS_PPI_ORG = '301'
KOSIS_PPI_TBL = 'DT_404Y014'      # 생산자물가지수（기본분류，BOK 归总）
KOSIS_PPI_BASE_PERIOD = '2020=100'
KR_PPI_EARLIEST = '2013-01'

KOSIS_IIP_ORG = '101'
KOSIS_IIP_TBL = 'DT_1F02001'      # 광공업생산지수
KOSIS_IIP_BASE_PERIOD = '2020=100'
KR_IIP_EARLIEST = '2018-01'
KOSIS_IIP_ITEMS = ('T10', 'T20')  # 생산지수 원지수/계절조정

_MONTH_RE = re.compile(r'M(\d{6})')
_PROVISIONAL_RE = re.compile(r'\s*p\)\s*$')
_PPI_SUFFIX_RE = re.compile(r'\s*\([^)]*=\s*100\)\s*$')
_NATION_ROW_PREFIX = 'T10 '
_IIP_NATION_PREFIX = '00 '
_IIP_ITEM_CODE_RE = re.compile(r'^(T\d+)(\s|$)')


def _ym_to_month(ym: str) -> str | None:
    if len(ym) != 6 or not ym.isdigit():
        return None
    return f'{ym[:4]}-{ym[4:]}'


def _extend_start(start: str, metric: str) -> str:
    """yoy/mom 派生需要基期数据，把起始月前移（yoy 12 个月 / mom 1 个月）。"""
    if metric == 'yoy':
        shift = 12
    elif metric == 'mom':
        shift = 1
    else:
        return start
    y, m = int(start[:4]), int(start[5:7])
    m -= shift
    while m <= 0:
        y -= 1
        m += 12
    return f'{y}-{m:02d}'


def _month_range(start: str, end: str) -> list[str]:
    """'YYYY-MM' 闭区间枚举（升序）。"""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out: list[str] = []
    while (sy, sm) <= (ey, em):
        out.append(f'{sy}-{sm:02d}')
        sm += 1
        if sm == 13:
            sy, sm = sy + 1, 1
    return out


def _shard_months(months: list[str]) -> list[list[str]]:
    """按自然年分片（1 万格下载上限）。"""
    shards: dict[str, list[str]] = {}
    for ym in months:
        shards.setdefault(ym[:4], []).append(ym)
    return [shards[y] for y in sorted(shards)]


def _parse_wide_csv(text: str, strip_ppi_suffix: bool
                    ) -> dict[str, dict[str, float]]:
    """KOSIS 宽表 CSV（1 行表头）→ {name: {month: level}}（전국行）。

    cpi 专用（ppi 见 ``parse_kosis_ppi_csv``）：表头行任一列命中 'M<YYYYMM>'；
    行首 T10 전국 过滤；科目列 '<code> <name>' → 净名。
    """
    reader = csv.reader(io.StringIO(text))
    months: list[tuple[int, str]] = []
    levels: dict[str, dict[str, float]] = {}
    for row in reader:
        if not row or len(row) < 3:
            continue
        header_months = []
        for i, cell in enumerate(row):
            m = _MONTH_RE.search(cell or '')
            if m:
                month = _ym_to_month(m.group(1))
                if month:
                    header_months.append((i, month))
        if header_months:
            months = header_months
            continue
        if not months:
            continue
        region = (row[0] or '').strip()
        if not region.startswith(_NATION_ROW_PREFIX):
            continue
        label = (row[1] or '').strip()
        code, sep, name = label.partition(' ')
        if not sep or not code or not name:
            continue
        if strip_ppi_suffix:
            name = _PPI_SUFFIX_RE.sub('', name)
        d = levels.setdefault(name, {})
        for i, month in months:
            if i >= len(row):
                continue
            raw = (row[i] or '').strip()
            if not raw or raw == '-':
                continue
            raw = _PROVISIONAL_RE.sub('', raw)
            try:
                d[month] = float(raw)
            except ValueError:
                continue
    return {k: d for k, d in levels.items() if d}


def parse_kosis_ppi_csv(text: str) -> dict[str, dict[str, float]]:
    """KOSIS DT_404Y014（생산자물가지수 기본분류）导出 CSV → {name: {month: level}}。

    与 cpi 不同：PPI 无 시도별 轴——列 0 即科目标签
    '<ACC_CD>.<code> <name> (weight 2020=100)'，列 1+ 为月份值。
    """
    reader = csv.reader(io.StringIO(text))
    months: list[tuple[int, str]] = []
    levels: dict[str, dict[str, float]] = {}
    for row in reader:
        if not row or len(row) < 2:
            continue
        header_months = []
        for i, cell in enumerate(row):
            m = _MONTH_RE.search(cell or '')
            if m:
                month = _ym_to_month(m.group(1))
                if month:
                    header_months.append((i, month))
        if header_months:
            months = header_months
            continue
        if not months:
            continue
        label = (row[0] or '').strip()
        code, sep, name = label.partition(' ')
        if not sep or not code or not name:
            continue
        name = _PPI_SUFFIX_RE.sub('', name)
        d = levels.setdefault(name, {})
        for i, month in months:
            if i >= len(row):
                continue
            raw = (row[i] or '').strip()
            if not raw or raw == '-':
                continue
            raw = _PROVISIONAL_RE.sub('', raw)
            try:
                d[month] = float(raw)
            except ValueError:
                continue
    return {k: d for k, d in levels.items() if d}


def parse_kosis_iip_csv(text: str
                        ) -> tuple[dict[str, dict[str, float]],
                                   dict[str, dict[str, float]]]:
    """KOSIS DT_1F02001 导出 CSV → (원지수 levels, 계절조정 levels)。

    表头 2 行：月份行 + 项目子列行（'T10 생산지수(원지수)'）；行首
    '00 ' 전국 过滤；只取 T10/T20 两项目列。
    """
    reader = csv.reader(io.StringIO(text))
    months: list[tuple[int, str]] = []
    item_map: dict[int, str] = {}
    levels: dict[str, dict[str, float]] = {}
    levels_sa: dict[str, dict[str, float]] = {}
    for row in reader:
        if not row or len(row) < 3:
            continue
        header_months = []
        for i, cell in enumerate(row):
            m = _MONTH_RE.search(cell or '')
            if m:
                month = _ym_to_month(m.group(1))
                if month:
                    header_months.append((i, month))
        if header_months:
            months = header_months
            continue
        if months and not item_map:
            row_items = {}
            for i, cell in enumerate(row):
                if i < 2:
                    continue
                im = _IIP_ITEM_CODE_RE.match((cell or '').strip())
                if im:
                    row_items[i] = im.group(1)
            if row_items:
                item_map = row_items
                continue
        if not months or not item_map:
            continue
        region = (row[0] or '').strip()
        if not region.startswith(_IIP_NATION_PREFIX):
            continue
        label = (row[1] or '').strip()
        code, sep, name = label.partition(' ')
        if not sep or not code or not name:
            continue
        for i, month in months:
            item = item_map.get(i)
            if item not in KOSIS_IIP_ITEMS:
                continue
            if i >= len(row):
                continue
            raw = (row[i] or '').strip()
            if not raw or raw == '-':
                continue
            raw = _PROVISIONAL_RE.sub('', raw)
            try:
                val = float(raw)
            except ValueError:
                continue
            d = levels if item == 'T10' else levels_sa
            d.setdefault(name, {})[month] = val
    return ({k: d for k, d in levels.items() if d},
            {k: d for k, d in levels_sa.items() if d})


def _fetch_sharded(org: str, tbl: str, start: str, end: str,
                   parse, export_kwargs: dict | None = None,
                   ) -> dict[str, dict[str, float]]:
    """按年分片导出 + 解析合并的公共管线。"""
    months = _month_range(start, end)
    if not months:
        return {}
    merged: dict[str, dict[str, float]] = {}
    with KosisSession(proxy=_proxy_url()) as ks:
        for shard in _shard_months(months):
            t0 = time.time()
            csv_text, _ = ks.export_csv(
                org, tbl, months=[m.replace('-', '') for m in shard],
                **(export_kwargs or {}))
            part = parse(csv_text)
            for key, d in part.items():
                merged.setdefault(key, {}).update(d)
            _log.info('kosis %s 分片 %s-%s 完成：%s 科目 %.1fs',
                      tbl, shard[0], shard[-1], len(part), time.time() - t0)
    if not merged:
        raise RuntimeError(f'KOSIS {tbl} 导出无数据')
    return merged


def fetch_korea_cpi(start: str, end: str) -> dict[str, dict[str, float]]:
    """KOSIS DT_1J22001 → {name: {month: level}}（전국，지출목적별全科目）。"""
    return _fetch_sharded(KOSIS_CPI_ORG, KOSIS_CPI_TBL,
                          max(start, KR_CPI_EARLIEST), end,
                          lambda t: _parse_wide_csv(t, strip_ppi_suffix=False),
                          export_kwargs={'class_ids': {'1': ['T10']}})


def fetch_korea_ppi(start: str, end: str) -> dict[str, dict[str, float]]:
    """KOSIS DT_404Y014 → {name: {month: level}}（생산자물가지수 기본분류）。"""
    return _fetch_sharded(KOSIS_PPI_ORG, KOSIS_PPI_TBL,
                          max(start, KR_PPI_EARLIEST), end,
                          parse_kosis_ppi_csv)


def fetch_korea_iip(start: str, end: str
                    ) -> dict[str, dict[str, dict[str, float]]]:
    """KOSIS DT_1F02001 → {'level': {...}, 'level_sa': {...}}（전국）。

    双 metric（원지수/계절조정）不走单层公共管线，单独分片处理。
    """
    months = _month_range(max(start, KR_IIP_EARLIEST), end)
    if not months:
        return {'level': {}, 'level_sa': {}}
    out: dict[str, dict[str, dict[str, float]]] = {'level': {}, 'level_sa': {}}
    with KosisSession(proxy=_proxy_url()) as ks:
        for shard in _shard_months(months):
            csv_text, _ = ks.export_csv(
                KOSIS_IIP_ORG, KOSIS_IIP_TBL,
                months=[m.replace('-', '') for m in shard],
                item_ids=list(KOSIS_IIP_ITEMS),
                class_ids={'1': ['00']})
            part, part_sa = parse_kosis_iip_csv(csv_text)
            for key, d in part.items():
                out['level'].setdefault(key, {}).update(d)
            for key, d in part_sa.items():
                out['level_sa'].setdefault(key, {}).update(d)
    if not any(out.values()):
        raise RuntimeError('KOSIS DT_1F02001 导出无数据')
    return out


def current_month() -> str:
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
