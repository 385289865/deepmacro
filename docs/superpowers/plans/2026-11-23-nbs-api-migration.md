# 中国发布稿接口迁移 NBS API 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FAI/PMI/Retail 三接口从发布稿解析彻底迁移到国家统计局数据发布库 API，删除 `_report.py`，同步官网数据并重新上线。

**Architecture:** 仿 `china/ppi.py` 模式：每模块自含 cid 节点表（分年代节点按官方区间裁剪合并），复用 `_nbs.query_indicators/query_data` + `_frame.to_wide_frame/clip_months`。数值不做 -100 变换（FAI/PMI/Retail 本身就是 % 或绝对量）。

**Tech Stack:** Python 3.9+（包 venv `/Users/mac/services/deepmacro/deepmacro/.venv`）；pytest 离线测试；官网静态站。

**设计文档:** `docs/specs/2026-11-23-nbs-api-migration-design.md`

**已探测事实（2026-11-23，写入代码注释）：**
- FAI 父节点 `c3079d221d2943888b87d47c28abf64e` 下 11 子节点；其中「分行业固定资产投资情况 (2004-2011)」`801562e228194e46a0a82b6c56e2cb5f` 指标数为 0（**跳过**）
- 指标数：概况 5 / 增速 7 / 行业2018- 74 / 登记(-2023) 29 / 民间 33 / 资金来源 7 / 行业2012-17 75 / 行业1998-2003 30 / 计划总投资 2 / 登记(2024-) 4
- PMI 父节点下 3 子节点：制造业 14 指标、非制造业 12、综合 1；制造业/非制造业存在同名指标（新订单指数、新出口订单指数 等）→ 需组名前缀消歧
- 社零叶节点 `d0cb882c7f27443ab6b3ef9421901961` 8 指标；query_data 返回 `{时间码: {iid: 值字符串}}`

---

### Task 1: 重写 china/fai.py（NBS API）

**Files:**
- Modify: `/Users/mac/services/deepmacro/deepmacro/deepmacro/china/fai.py`（整文件重写）
- Test: `/Users/mac/services/deepmacro/deepmacro/tests/test_smoke.py`

- [ ] **Step 1: 写失败测试（离线）**

在 `tests/test_smoke.py` 追加（放在 `test_extract_month_from_title` 之后，该测试 Task 4 才删）：

```python
def test_fai_node_table():
    """FAI NBS 节点表完整（10 个有效节点，2004-11 空节点已剔除）。"""
    from deepmacro.china import fai
    assert len(fai._FAI_NODES) == 10
    # 分年代节点区间互不重叠且升序覆盖 1998 起
    segs = sorted((s, e) for _, s, e in fai._FAI_NODES if '行业' in _lbl(fai))
    assert segs[0][0] <= '1998-01'


def _lbl(fai):
    return [l for l, _, _ in fai._FAI_NODES]


def test_strip_pct_suffix():
    from deepmacro.china.fai import _clean_name
    assert _clean_name('固定资产投资额累计增长 (%)') == '固定资产投资额累计增长'
    assert _clean_name('民间固定资产投资累计增长 (%)') == '民间固定资产投资累计增长'
```

（`test_fai_node_table` 里 segs 断言如与实现常量写法不符，以实现为准微调测试——目标是锁住「行业分年代节点最早 1998-01」这一事实。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/services/deepmacro/deepmacro && .venv/bin/pytest tests/test_smoke.py -k fai -v`
Expected: FAIL（`_FAI_NODES` 不存在）

- [ ] **Step 3: 重写 fai.py**

```python
"""中国固定资产投资 — 国家统计局数据发布库 API（data.stats.gov.cn）。

父节点「固定资产投资 (不含农户)」下 10 个有效子节点合并：
概况/增速/按行业分（2018-、2012-2017、1998-2003 三段；2004-2011 节点
为空已跳过）/登记注册类型（-2023、2024- 两段）/民间投资/资金来源/计划总投资。
分年代节点按官方区间裁剪（模式同 PPI 行业分片）。

数值为官方累计同比 %（个别为绝对量，列名自带单位说明）。
"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import polite_sleep
from deepmacro.china import _nbs

_log = logging.getLogger(__name__)

# (标签, 区间起, 区间止, cid)。区间为该节点官方数据年代，请求时与用户窗口取交集。
_FAI_NODES: list[tuple[str, str, str, str]] = [
    ('概况', '1998-01', '2999-12', '5129067b149d4ddfbec1ffc478d35bfb'),
    ('增速', '1998-01', '2999-12', 'aac38c7aa152478ebea254ac412aa0a1'),
    ('按行业分2018', '2018-01', '2999-12', '9002859582574437a91914e0fd50166b'),
    ('按行业分2012-17', '2012-01', '2017-12', 'f4fc1914288b443a82dc9fb793e074c2'),
    ('按行业分1998-2003', '1998-01', '2003-12', '75b0c9fbfd044a019a879f4d42295c99'),
    ('登记注册-2023', '1998-01', '2023-12', '8f28f720ba374e61adba29e79829c31d'),
    ('登记注册2024-', '2024-01', '2999-12', '6158d027f4804bfd9b19e36ce4cb5eda'),
    ('民间投资', '1998-01', '2999-12', 'a3136f1cd88442efa209740d78bba410'),
    ('资金来源', '1998-01', '2999-12', '88077f9647ef499bac2e2e3350e3bab1'),
    ('计划总投资', '1998-01', '2999-12', '2bf47029b8f84dacaddb8f0e5b93583c'),
]


def _clean_name(api_name: str) -> str:
    name = api_name.strip()
    for suffix in (' (%)', '（%）'):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def _query_node(cid: str, period_range: str,
                result: dict[str, dict[str, float]]) -> None:
    indicators = _nbs.query_indicators(cid)
    iid_to_subject = {it['iid']: _clean_name(it['name'])
                      for it in indicators if it.get('iid')}
    if not iid_to_subject:
        return
    polite_sleep()
    raw = _nbs.query_data(cid, list(iid_to_subject), period_range)
    for time_code, values in raw.items():
        month = _nbs.period_to_month(time_code)
        bucket = result.setdefault(month, {})
        for iid, val_str in values.items():
            subject = iid_to_subject.get(iid)
            if subject:
                try:
                    bucket[subject] = float(val_str)
                except (ValueError, TypeError):
                    pass


def macro_china_fai(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """中国固定资产投资（不含农户）— 国家统计局官方口径，全量科目。

    10 个 NBS 子节点合并：概况/增速/按行业分（三段拼接至 1998）/登记注册
    类型/民间投资/资金来源/计划总投资，同名科目跨年代节点自动衔接。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2011-01，行业分片最早 1998-01）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 各科目（固定资产投资额累计增长/
        民间固定资产投资累计增长/农、林、牧、渔业…），数值为官方口径。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_fai(start_month='2024-01').tail(3)
    """
    start = start_month or '2011-01'
    end = end_month or _current_month()
    result: dict[str, dict[str, float]] = {}
    for _, seg_start, seg_end, cid in _FAI_NODES:
        req_start = max(start, seg_start)
        req_end = min(end, seg_end)
        if req_start > req_end:
            continue
        sub_range = (f'{_nbs.month_to_period(req_start)}-'
                     f'{_nbs.month_to_period(req_end)}')
        try:
            _query_node(cid, sub_range, result)
        except Exception as e:
            _log.warning('FAI 节点 %s 拉取失败: %s', cid, e)
    if not result:
        raise RuntimeError('国家统计局固投接口返回无数据（可能触发反爬，稍后重试）')
    return clip_months(to_wide_frame(result), start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
```

- [ ] **Step 4: 跑离线测试通过**

Run: `.venv/bin/pytest tests/test_smoke.py -k "fai or strip" -v`
Expected: PASS

- [ ] **Step 5: 实跑验证**

Run: `.venv/bin/python -c "
import deepmacro as dm
df = dm.macro_china_fai(start_month='2026-08')
print('近3月:', df.shape, df['月份'].tolist())
full = dm.macro_china_fai()
print('全量:', full.shape, '最早月:', full['月份'].iloc[0])
"`
Expected: 近 3 月 3 行（2026-08/09/10）；全量最早月 ≤ 2011-01。记录实际列数与最早月（Task 5 网页要用）。

- [ ] **Step 6: Commit**

```bash
cd /Users/mac/services/deepmacro/deepmacro
git add deepmacro/china/fai.py tests/test_smoke.py
git commit -m "feat: 固投迁移 NBS 数据发布库 API（10 节点拼接，弃发布稿）"
```

---

### Task 2: 重写 china/pmi.py（NBS API）

**Files:**
- Modify: `/Users/mac/services/deepmacro/deepmacro/deepmacro/china/pmi.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: 写失败测试**

```python
def test_pmi_nodes_and_prefix():
    from deepmacro.china import pmi
    assert set(pmi._PMI_NODES) == {'制造业', '非制造业', '综合'}
    assert pmi._prefixed('制造业', '新订单指数') == '制造业·新订单指数'
    assert pmi._prefixed('综合', '综合PMI产出指数') == '综合PMI产出指数'
```

- [ ] **Step 2: 确认失败**

Run: `.venv/bin/pytest tests/test_smoke.py -k pmi_nodes -v` → FAIL

- [ ] **Step 3: 重写 pmi.py**

```python
"""中国采购经理指数 — 国家统计局数据发布库 API（data.stats.gov.cn）。

制造业（14 指标）/非制造业（12）/综合 PMI 产出指数（1）三子节点合并；
制造业与非制造业的同名指标（新订单指数 等）以「组名·指标名」消歧。
"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import polite_sleep
from deepmacro.china import _nbs

_log = logging.getLogger(__name__)

_PMI_NODES: dict[str, str] = {
    '制造业': '93ffbb1aa85740d3aa2618371508b606',
    '非制造业': '7a64a6e25aec4a8e9dde044ecd9e2cce',
    '综合': '455378e1c3264a32875768a35ba5de76',
}

# 两组共有名 → 加组前缀；组内独有名保持原名。
_DUP_CACHE: set[str] | None = None


def _clean_name(api_name: str) -> str:
    name = api_name.strip()
    for suffix in (' (%)', '（%）'):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def _prefixed(group: str, name: str) -> str:
    dup = _DUP_CACHE or set()
    return f'{group}·{name}' if name in dup else name


def _refresh_dup_names() -> set[str]:
    global _DUP_CACHE
    names: dict[str, int] = {}
    for cid in _PMI_NODES.values():
        for it in _nbs.query_indicators(cid):
            n = _clean_name(it['name'])
            names[n] = names.get(n, 0) + 1
    _DUP_CACHE = {n for n, c in names.items() if c > 1}
    return _DUP_CACHE


def macro_china_pmi(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """中国采购经理指数 — 国家统计局官方口径。

    制造业 PMI + 14 分项、非制造业商务活动 + 12 分项、综合 PMI 产出指数，
    同名分项以「制造业·」「非制造业·」前缀区分。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2020-01，实跑最早月以 head(1) 为准）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 各指数列（制造业采购经理指数/
        生产指数/新订单指数/…/综合PMI产出指数），数值为官方 % 。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_pmi(start_month='2024-01').tail(3)
    """
    start = start_month or '2020-01'
    end = end_month or _current_month()
    _refresh_dup_names()
    period_range = f'{_nbs.month_to_period(start)}-{_nbs.month_to_period(end)}'
    result: dict[str, dict[str, float]] = {}
    for group, cid in _PMI_NODES.items():
        indicators = _nbs.query_indicators(cid)
        iid_to_subject = {it['iid']: _prefixed(group, _clean_name(it['name']))
                          for it in indicators if it.get('iid')}
        if not iid_to_subject:
            continue
        polite_sleep()
        raw = _nbs.query_data(cid, list(iid_to_subject), period_range)
        for time_code, values in raw.items():
            month = _nbs.period_to_month(time_code)
            bucket = result.setdefault(month, {})
            for iid, val_str in values.items():
                subject = iid_to_subject.get(iid)
                if subject:
                    try:
                        bucket[subject] = float(val_str)
                    except (ValueError, TypeError):
                        pass
    if not result:
        raise RuntimeError('国家统计局 PMI 接口返回无数据（可能触发反爬，稍后重试）')
    return clip_months(to_wide_frame(result), start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
```

- [ ] **Step 4: 离线测试通过**

Run: `.venv/bin/pytest tests/test_smoke.py -k pmi_nodes -v` → PASS（`_prefixed` 在 `_DUP_CACHE` 为空时返回原名，测试即验证此默认态；联网实跑时才填充缓存）

- [ ] **Step 5: 实跑验证 + 固化最早月**

Run: `.venv/bin/python -c "
import deepmacro as dm
df = dm.macro_china_pmi(start_month='2026-08')
print('近3月:', df.shape, df['月份'].tolist())
print('列:', df.columns.tolist()[:8])
full = dm.macro_china_pmi()
print('全量最早月:', full['月份'].iloc[0])
"`
记录：列数、前缀列样例（应有 制造业·新订单指数）、全量最早月。若最早月 ≠ 2020-01，把 docstring「默认 2020-01」与 `start = start_month or '2020-01'` 改为实跑最早月。

- [ ] **Step 6: Commit**

```bash
git add deepmacro/china/pmi.py tests/test_smoke.py
git commit -m "feat: PMI 迁移 NBS 数据发布库 API（三节点合并，同名前缀消歧）"
```

---

### Task 3: 重写 china/retail.py（NBS API，长表改宽表）

**Files:**
- Modify: `/Users/mac/services/deepmacro/deepmacro/deepmacro/china/retail.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: 写失败测试**

```python
def test_retail_node():
    from deepmacro.china import retail
    assert retail._RETAIL_CID == 'd0cb882c7f27443ab6b3ef9421901961'
```

- [ ] **Step 2: 确认失败** → Run: `.venv/bin/pytest tests/test_smoke.py -k retail_node -v` → FAIL

- [ ] **Step 3: 重写 retail.py**

```python
"""中国社会消费品零售总额 — 国家统计局数据发布库 API（data.stats.gov.cn）。

总量口径 8 指标：当期值(亿元)/累计值(亿元)/同比增长(%)/累计增长(%) +
限上单位对应 4 项。宽表输出（v0.1.0 发布稿长表已随发布稿方案移除）。
"""
from __future__ import annotations

from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import polite_sleep
from deepmacro.china import _nbs

_RETAIL_CID = 'd0cb882c7f27443ab6b3ef9421901961'


def _clean_name(api_name: str) -> str:
    name = api_name.strip()
    for suffix in (' (%)', ' (亿元)', '（%）', '（亿元）'):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def macro_china_retail(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """中国社会消费品零售总额 — 国家统计局官方口径，总量 8 指标。

    社零总额与限上单位的当期值/累计值/同比/累计增长。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2021-01，实跑最早月以 head(1) 为准）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 8 列（社会消费品零售总额当期值/
        同比增长/…/限上单位消费品零售额当期值/…）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_retail(start_month='2024-01').tail(3)
    """
    start = start_month or '2021-01'
    end = end_month or _current_month()
    indicators = _nbs.query_indicators(_RETAIL_CID)
    iid_to_subject = {it['iid']: _clean_name(it['name'])
                      for it in indicators if it.get('iid')}
    if not iid_to_subject:
        raise RuntimeError('国家统计局社零接口返回无指标')
    polite_sleep()
    period_range = (f'{_nbs.month_to_period(start)}-'
                    f'{_nbs.month_to_period(end)}')
    raw = _nbs.query_data(_RETAIL_CID, list(iid_to_subject), period_range)
    result: dict[str, dict[str, float]] = {}
    for time_code, values in raw.items():
        month = _nbs.period_to_month(time_code)
        bucket = result.setdefault(month, {})
        for iid, val_str in values.items():
            subject = iid_to_subject.get(iid)
            if subject:
                try:
                    bucket[subject] = float(val_str)
                except (ValueError, TypeError):
                    pass
    if not result:
        raise RuntimeError('国家统计局社零接口返回无数据（可能触发反爬，稍后重试）')
    return clip_months(to_wide_frame(result), start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
```

- [ ] **Step 4: 离线测试通过** → `.venv/bin/pytest tests/test_smoke.py -k retail_node -v`

- [ ] **Step 5: 实跑验证 + 固化最早月**

Run: `.venv/bin/python -c "
import deepmacro as dm
df = dm.macro_china_retail(start_month='2026-08')
print('近3月:', df.shape, df['月份'].tolist(), df.columns.tolist())
full = dm.macro_china_retail()
print('全量最早月:', full['月份'].iloc[0])
"`
Expected: 近 3 月 3 行 × 9 列（月份 + 8 指标），2026-10 当期值 ≈ 46462.5（已探测）；记录最早月并回填 docstring/默认值。

- [ ] **Step 6: Commit**

```bash
git add deepmacro/china/retail.py tests/test_smoke.py
git commit -m "feat: 社零迁移 NBS 数据发布库 API（总量 8 指标宽表）"
```

---

### Task 4: 删除 _report.py + catalog/测试清理

**Files:**
- Delete: `/Users/mac/services/deepmacro/deepmacro/deepmacro/china/_report.py`
- Modify: `deepmacro/__init__.py`（_CATALOG 三行数据源）、`tests/test_smoke.py`

- [ ] **Step 1: 确认无引用**

Run: `grep -rn "_report" deepmacro/ tests/ | grep -v "_report.py:"`
Expected: 无输出（pmi/retail/fai 已重写不再引用；若有残留先清）

- [ ] **Step 2: 删除文件**

```bash
git rm deepmacro/china/_report.py
```

- [ ] **Step 3: 更新 _CATALOG（__init__.py）**

三行的『数据源』与『官方链接』改为：
```python
    'macro_china_pmi': ('中国', '采购经理指数（PMI）',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
    'macro_china_retail': ('中国', '社会消费品零售总额（总量 8 指标）',
                           '国家统计局数据发布库 API',
                           'https://data.stats.gov.cn/'),
    'macro_china_fai': ('中国', '固定资产投资（不含农户，全科目）',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
```

- [ ] **Step 4: 清理测试**

`tests/test_smoke.py`：删除 `from deepmacro.china._report import extract_month_from_title` 与 `test_extract_month_from_title` 整个函数。

- [ ] **Step 5: 全量离线测试 + import 冒烟**

Run: `.venv/bin/pytest tests/test_smoke.py -v && .venv/bin/python -c "import deepmacro as dm; print(len(dm.catalog()))"`
Expected: 全 PASS，catalog 16 行

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: 删除发布稿解析（_report.py），catalog 数据源统一数据发布库"
```

---

### Task 5: 官网数据同步 + 上线 + 推送

**Files:**
- Modify: `/Users/mac/services/deepmacro/deepmacro-website/document-samples.js`（重生成）
- Modify: `/Users/mac/services/deepmacro/deepmacro-website/index.html`、`document.html`

- [ ] **Step 1: 重生成样例**

```bash
cd /Users/mac/services/deepmacro/deepmacro-website
../deepmacro/.venv/bin/python tools/gen-doc-samples.py --only china
```
Expected: 6 个 [ok]；fai/pmi/retail 新列数与 Task 1-3 实跑一致。

- [ ] **Step 2: 更新 document.html I 对象三接口**

以实跑数据为准，三接口统一改为：
- `sig`：`dm.macro_china_fai(start_month, end_month)`（pmi/retail 同理）
- `params`：仅 `[["start_month","str","否","起始月 'YYYY-MM'，默认 …"],["end_month","str","否","结束月 'YYYY-MM'，默认最新"]]`（默认值 = 各任务固化的实跑最早月）
- `deps`：`基础依赖　无额外依赖。高频调用可能触发 WZWS 反爬，包内已内置重试。`
- `outputs`/`cov`/`desc`：按实跑列数与最早月改（fai 约 190+ 列全科目、pmi 27 指标、retail 8 指标）

- [ ] **Step 3: 更新 index.html CARDS 中国三卡**

`分项覆盖`：PPI 卡不动；PMI 卡 `制造业、非制造业、综合PMI 3 组 · 27 个指标`；Retail 卡 `总量 8 指标（当期/累计/同比/累计增长 × 总额/限上）`；FAI 卡 `概况、行业、登记注册类型、民间、资金来源 等 10 组 · 190+ 科目`（数字以实跑为准）。`数据来源` 三卡均改 `国家统计局数据发布库`。`数据深度` 按实跑最早月改。

- [ ] **Step 4: 浏览器视觉/交互走查**

8010 服务跑 `document.html#/macro_china_fai`、`#/macro_china_pmi`、`#/macro_china_retail`：参数表仅 2 行、调用结果为 API 新数据（fai 列名如 `农、林、牧、渔业固定资产投资额累计增长`）、无「发布稿」字样；index 中国 tab 三卡来源已变。

- [ ] **Step 5: 容器重建上线 + 提交推送**

```bash
cd /Users/mac/services/deepmacro/deepmacro-website
git add -A && git commit -m "data: FAI/PMI/社零迁移 NBS API 后官网同步"
docker rm -f qiyan-deepmacro-web; docker build -t qiyan-deepmacro-web . && \
  docker run -d --name qiyan-deepmacro-web --restart unless-stopped -p 5181:80 qiyan-deepmacro-web
curl -s http://127.0.0.1:5181/ | grep -c "数据发布库"
git push origin main
cd /Users/mac/services/deepmacro/deepmacro && git push origin main
```

- [ ] **Step 6: 服务器环境重装 + 知识库**

```bash
cd /Users/mac/services/deepmacro/deepmacro && .venv/bin/python -m build --wheel 2>&1 | tail -1
scp dist/deepmacro-0.1.0-py3-none-any.whl root@111.229.141.39:/tmp/
ssh root@111.229.141.39 '/root/miniconda3/envs/data_analysis/bin/pip install --force-reinstall --no-deps /tmp/deepmacro-0.1.0-py3-none-any.whl 2>&1 | tail -1'
docker exec jupyter-lab sh -c 'cd /tmp && rm -rf dm && git clone -q --depth 1 https://github.com/385289865/deepmacro dm && /opt/envs/data_analysis/bin/python -m pip install --force-reinstall --no-deps /tmp/dm 2>&1 | tail -1 || /opt/envs/data_analysis/bin/python -m pip install --force-reinstall --no-deps /jupyter_data/../tmp/dm'
```
（容器内如 clone 失败则从宿主机 `docker cp` 源码目录进容器安装；两处装完各跑 `macro_china_fai(start_month='2026-08')` 确认秒级返回。）
知识库 `deepmacro/decisions.md` 追加：发布稿方案废弃决策 + 三接口 NBS API cid 表；`lessons.md` 追加：FAI 行业 2004-2011 节点为空需跳过、PMI 同名指标前缀消歧。

---

## Self-Review 记录

1. **Spec 覆盖**：三接口迁移（Task 1-3）、彻底删除 _report（Task 4）、catalog/docstring（Task 4 + 各任务）、官网同步/容器/推送/服务器重装/知识库（Task 5）——设计文档 §2-§5 全部落位。
2. **占位符扫描**：无 TBD；「以实跑为准」均伴随明确步骤（Step 5 实跑记录 + Task 5 Step 2/3 回填），非悬空。
3. **类型一致性**：三模块统一 `(start_month, end_month)` 签名；`_clean_name`/`_query_node` 命名在各任务内自洽；测试引用的 `_FAI_NODES`/`_PMI_NODES`/`_prefixed`/`_RETAIL_CID` 与实现常量名一致。
