"""日本官方源 — e-Stat 消費者物価指数（総務省统计局）+ BOJ 国内企業物価指数。

e-Stat CPI：政府統計コード 00200573，xlsx（表1-1 中分類指数（全国）78 系列 +
表4-1 品目別価格指数（全国）4 分片）；同文件 3 sheet（指数/前月比/前年同月比），
只取指数 sheet，全史列（1970 起，2025 年基準链回溯）。

BOJ PPI（cgpi）：https://www.stat-search.boj.or.jp/info/cgpi_m_jp.zip 内
单 shift_jis CSV（2020 基準，约 2020-01 起）。
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from typing import Any

from deepmacro._http import download_file, request_direct_first

_log = logging.getLogger(__name__)

CGPI_ZIP_URL = 'https://www.stat-search.boj.or.jp/info/cgpi_m_jp.zip'
CGPI_SOURCE_URL = 'https://www.stat-search.boj.or.jp/'

ESTAT_API_URL = 'https://www.e-stat.go.jp/retrieve/api_file'
ESTAT_TOUKEI = '00200573'
ESTAT_FILE_URL = ('https://www.e-stat.go.jp/stat-search/file-download'
                  '?statInfId={stat_inf_id}&fileKind=0')
ESTAT_SOURCE_URL = 'https://www.e-stat.go.jp/'

_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}


# ============================================================
# BOJ 国内企業物価指数（PPI）
# ============================================================

def _subject_name(raw_path: str) -> str:
    """C 列分類パス → 科目名（'[国内企業物価指数] 総平均' → '総平均' 等）。"""
    s = raw_path.strip()
    if s.startswith('['):
        close = s.find(']')
        if close != -1:
            s = s[close + 1:].strip()
    if '/' in s:
        s = s.rsplit('/', 1)[-1]
    return s.lstrip('_').strip()


def parse_cgpi_csv(text: str) -> dict[str, dict[str, float]]:
    """BOJ cgpi CSV → {科目名: {'YYYY-MM': level}}（全系列）。"""
    out: dict[str, dict[str, float]] = {}
    reader = csv.reader(io.StringIO(text))
    months: list[tuple[int, str]] = []
    for row in reader:
        code = (row[0].strip() if row and row[0] else '')
        if not re.match(r'^PRCG', code):
            found = []
            for i, cell in enumerate(row):
                cell = cell.strip()
                if re.fullmatch(r'\d{6}', cell):
                    y, m = int(cell[:4]), int(cell[4:])
                    if 1 <= m <= 12:
                        found.append((i, f'{y:04d}-{m:02d}'))
            if found:
                months = found
            continue
        stat_name = row[1].strip() if len(row) > 1 else ''
        raw_path = row[2].strip() if len(row) > 2 else ''
        name = _subject_name(raw_path) if raw_path else stat_name
        if not name:
            continue
        d: dict[str, float] = {}
        for i, month in months:
            if i >= len(row):
                continue
            raw = (row[i] or '').strip()
            if not raw:
                continue
            try:
                d[month] = float(raw)
            except ValueError:
                continue
        if d:
            out[name] = d
    return out


def fetch_japan_ppi_levels() -> dict[str, dict[str, float]]:
    """下载 cgpi zip → 解析全系列 level（约 2020-01 起全史）。"""
    content = download_file(CGPI_ZIP_URL, headers=_BROWSER_HEADERS)
    z = zipfile.ZipFile(io.BytesIO(content))
    csv_name = next((n for n in z.namelist() if n.lower().endswith('.csv')), None)
    if csv_name is None:
        raise RuntimeError('BOJ zip 未找到 CSV 文件')
    text = z.read(csv_name).decode('shift_jis', errors='replace')
    levels = parse_cgpi_csv(text)
    if not levels:
        raise RuntimeError('BOJ cgpi CSV 解析为空')
    return levels


# ============================================================
# e-Stat 消費者物価指数（CPI）
# ============================================================

def _estat_headers() -> dict[str, str]:
    return {'User-Agent': _BROWSER_HEADERS['User-Agent'],
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'ja,en;q=0.8'}


def _estat_api(params: dict[str, str]) -> str:
    """e-Stat 检索 JSON 端点 → items HTML（免 key，直连优先代理降级）。"""
    resp = request_direct_first('GET', ESTAT_API_URL, timeout=60,
                                params=params, headers=_estat_headers())
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError as e:
        raise RuntimeError(f'e-Stat API 非 JSON 响应：{e}') from e
    return payload.get('items', '')


def _estat_rows(items: str) -> list[tuple[str, str]]:
    """items HTML 文件行 → [(statInfId, 行文本)]（fileKind=0 xlsx 行，保序）。"""
    out: list[tuple[str, str]] = []
    for chunk in re.split(r'stat-dataset_list-item', items)[1:]:
        m = re.search(r'statInfId=(\d+)&(?:amp;)?fileKind=0', chunk)
        if not m:
            continue
        text = re.sub(r'<[^>]+>', ' ', chunk).replace('&nbsp;', ' ')
        out.append((m.group(1), ' '.join(text.split())))
    return out


def estat_file_ids() -> list[str]:
    """e-Stat CPI 清单发现（4 步下钻动态解析，基準改版自动跟随）。

    統計種（tstat）→ 月次主表（cycle=1）→ 最新月 → 表1-1/表4-1（全国·月次）
    文件行。statInfId 每月变化，不硬编码。
    """
    items = _estat_api({'page': '1', 'layout': 'datalist', 'toukei': ESTAT_TOUKEI})
    m = re.search(r'tstat=(\d+)', items)
    if not m:
        raise RuntimeError('e-Stat 清单未找到統計種（tstat）链接')
    tstat = m.group(1)
    items = _estat_api({'page': '1', 'layout': 'datalist',
                        'toukei': ESTAT_TOUKEI, 'tstat': tstat})
    monthly = next((href for href in re.findall(r'href="([^"]+)"', items)
                    if 'cycle=1' in href), None)
    if not monthly:
        raise RuntimeError('e-Stat 清单未找到月次主表（cycle=1）链接')
    cycle = re.search(r'cycle=(\d+)', monthly).group(1)
    m_tc = re.search(r'tclass1=(\d+)', monthly)
    if not m_tc:
        raise RuntimeError(f'e-Stat 月次主表链接缺 tclass1：{monthly}')
    tclass1 = m_tc.group(1)
    items = _estat_api({'page': '1', 'layout': 'datalist', 'toukei': ESTAT_TOUKEI,
                        'tstat': tstat, 'cycle': cycle, 'tclass1': tclass1})
    months: list[tuple[tuple[int, int], str, str]] = []
    for mm in re.finditer(r'year=(\d+)&(?:amp;)?month=(\d+)', items):
        y, mo = mm.group(1), mm.group(2)
        try:
            months.append(((int(y[:-1]), int(mo[-2:])), y, mo))
        except (ValueError, IndexError):
            continue
    if not months:
        raise RuntimeError('e-Stat 月報清单未解析到月份链接')
    best = max(months, key=lambda x: x[0])
    items = _estat_api({'page': '1', 'layout': 'datalist', 'toukei': ESTAT_TOUKEI,
                        'tstat': tstat, 'cycle': cycle, 'tclass1': tclass1,
                        'year': best[1], 'month': best[2]})
    ids: list[str] = []
    for sid, text in _estat_rows(items):
        if not (('中分類指数（全国）' in text or '品目別価格指数（全国）' in text)
                and '月次' in text.split()):
            continue
        bang = re.search(r'表番号 ([0-9\-]+)', text)
        if bang and bang.group(1) in ('1-1', '4-1'):
            ids.append(sid)
    if not ids:
        raise RuntimeError('e-Stat 当月清单未找到表1-1/表4-1（全国·月次）文件')
    return ids


def _ym_to_month(ym: str) -> str | None:
    if len(ym) != 6 or not ym.isdigit():
        return None
    return f'{ym[:4]}-{ym[4:]}'


def parse_estat_xlsx(data: bytes) -> dict[str, dict[str, float]]:
    """e-Stat CPI xlsx（指数 sheet）→ {科目名: {'YYYY-MM': level}}。

    行9=符号（col13 起）、行10=名称、行15 起数据行；B 列 'YYYYMM' 字符串；
    缺失值 '- ' 跳过。非「（全国）」表題的文件整文件跳过。
    """
    from io import BytesIO
    from openpyxl import load_workbook
    ws = load_workbook(BytesIO(data), data_only=True).worksheets[0]
    title = str(ws.cell(row=6, column=9).value or '')
    if '（全国）' not in title:
        _log.warning('e-Stat 表題非全国（%s）——跳过', title)
        return {}
    CODE_ROW, NAME_ROW, DATA_FROM = 9, 10, 15
    series: dict[int, str] = {}
    for c in range(13, ws.max_column + 1):
        code = str(ws.cell(row=CODE_ROW, column=c).value or '').strip()
        name = str(ws.cell(row=NAME_ROW, column=c).value or '').strip()
        if code and name and re.fullmatch(r'\d{4}|\d{10}', code):
            series[c] = name
    out: dict[str, dict[str, float]] = {name: {} for name in series.values()}
    for r in range(DATA_FROM, ws.max_row + 1):
        month = _ym_to_month(str(ws.cell(row=r, column=2).value or '').strip())
        if not month:
            continue
        for c, name in series.items():
            v = ws.cell(row=r, column=c).value
            if v is None:
                continue
            if isinstance(v, (int, float)):
                out[name][month] = float(v)
            elif str(v).strip() not in ('', '-'):
                try:
                    out[name][month] = float(str(v).strip())
                except ValueError:
                    continue
    return {k: d for k, d in out.items() if d}


def fetch_japan_cpi_levels() -> dict[str, dict[str, float]]:
    """下载当月 e-Stat CPI 全部表1-1/表4-1 文件 → 合并 {科目名: {month: level}}。

    表1-1 中分類指数 78 系列 + 表4-1 品目別全细分（1970 起全史）。
    """
    levels: dict[str, dict[str, float]] = {}
    for sid in estat_file_ids():
        try:
            content = download_file(ESTAT_FILE_URL.format(stat_inf_id=sid),
                                    headers=_estat_headers())
            for name, d in parse_estat_xlsx(content).items():
                bucket = levels.setdefault(name, {})
                bucket.update(d)
        except Exception as e:
            _log.warning('e-Stat file %s failed: %s', sid, e)
    if not levels:
        raise RuntimeError('e-Stat CPI 解析为空')
    return levels


# ============================================================
# 水平值 → 宽表公共层
# ============================================================

def levels_to_frame(levels: dict[str, dict[str, float]], metric: str,
                    start: str, end: str):
    """{科目: {month: level}} → 按 metric 输出宽表 DataFrame。"""
    from deepmacro._frame import clip_months, to_wide_frame
    by_month: dict[str, dict[str, float]] = {}
    for name, d in levels.items():
        for month, v in d.items():
            by_month.setdefault(month, {})[name] = v
    if metric in ('yoy', 'mom'):
        by_month = _derive(by_month, metric)
    if not by_month:
        raise RuntimeError('数据源返回为空')
    return clip_months(to_wide_frame(by_month), start, end)


def _prev_month(month: str) -> str | None:
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f'{y - 1}-12'
    return f'{y}-{m - 1:02d}'


def _derive(by_month: dict[str, dict[str, float]], metric: str
            ) -> dict[str, dict[str, float]]:
    """由水平值派生 yoy/mom（%）。"""
    out: dict[str, dict[str, float]] = {}
    for month, subjects in by_month.items():
        y, m = month[:4], month[5:7]
        anchor = (f'{int(y) - 1}-{m}' if metric == 'yoy' else _prev_month(month))
        base = by_month.get(anchor, {})
        for name, v in subjects.items():
            prev = base.get(name)
            if prev not in (None, 0):
                out.setdefault(month, {})[name] = round((v / prev - 1) * 100, 2)
    return out


def default_range(start: str | None, end: str | None,
                  default_start: str) -> tuple[str, str]:
    from datetime import datetime
    now = datetime.now()
    return start or default_start, end or f'{now.year}-{now.month:02d}'
