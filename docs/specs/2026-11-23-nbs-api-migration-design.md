# 中国发布稿接口迁移 NBS API — 设计文档

日期：2026-11-23
状态：待用户评审
范围：deepmacro 包（`china/fai.py`、`china/pmi.py`、`china/retail.py` 重写为 NBS API；删除 `china/_report.py`）；官网两页数据同步

## 1. 背景与决策

v0.1.0 中 FAI/PMI/Retail 走 stats.gov.cn 发布稿解析——慢（~1 分钟）、脆（格式变更即失效）、历史短（仅近 3 期）。用户判定该方案错误，要求**彻底移除**，三接口全部改用国家统计局数据发布库 API（`data.stats.gov.cn`，即 `/dg/website/page.html#/pc/national/monthData` 页面的后端，与 CPI/PPI/IVA 同源）。

已实测确认 API 节点存在（2026-11-23 探测）：

| 指标 | cid 节点 | 内容 |
|---|---|---|
| 固定资产投资（不含农户） | `c3079d221d2943888b87d47c28abf64e` | 11 个子节点：概况、增速、按行业分（2018-）、按登记注册类型（-2023 / 2024- 两段）、民间投资按行业、资金来源、分行业（2012-2017 / 2004-2011 / 1998-2003 三段）、计划总投资 |
| 采购经理指数 | `8f108f3406d7436b82498390d9e08da7` | 3 个子节点：制造业、非制造业、综合 PMI 产出指数 |
| 社会消费品零售总额 | `d0cb882c7f27443ab6b3ef9421901961` | 8 个指标：当期值(亿元)/累计值(亿元)/同比增长(%)/累计增长(%) + 限上单位对应 4 项 |

社零损失 26 科目细分（API 为总量口径）——用户已接受（选择彻底移除发布稿）。

## 2. 接口设计

### 2.1 函数签名（破坏性变更，v0.1.0 alpha 期可接受）

```python
macro_china_fai(start_month=None, end_month=None)    # 无 metric：增速/绝对量作为不同列一并输出
macro_china_pmi(start_month=None, end_month=None)
macro_china_retail(start_month=None, end_month=None)
```

- 移除 `metric` 与 `max_reports` 参数（无发布稿、无口径切换）
- 返回宽表 DataFrame：首列 `月份`，其余为科目列（与 CPI/PPI/IVA 一致）
- 社零从长表改回宽表（8 列），移除长表特例

### 2.2 数据组装

复用 `_nbs.py` 既有设施（`query_indicators` / `query_data` / 分段节点按官方年代裁剪区间，模式同 PPI 行业分片）：

- **FAI**：11 个子节点逐一取指标合并为宽表；分年代节点（2012-2017、2004-2011、1998-2003、-2023 登记注册）按其官方区间裁剪，列名冲突时以后缀区分（如 `按行业分_制造业`）
- **PMI**：3 个子节点（制造业/非制造业/综合）指标合并；默认 `start_month='2020-01'`（API 实际最早月以探测为准，实现时确认）
- **Retail**：单节点 8 指标直接输出，默认 `start_month='2021-01'`（同上，以实跑最早月为准）

默认起始月实现时以实跑 `head(1)` 校准后固化到 docstring。

### 2.3 删除清单

- `deepmacro/china/_report.py`（发布稿扫描/解析全套）
- `china/pmi.py`、`retail.py`、`fai.py` 中发布稿逻辑（整文件重写）
- `__init__.py` `_CATALOG` 三接口「数据源」改为「国家统计局数据发布库 API」
- `tests/test_smoke.py` 中 `extract_month_from_title` 相关测试
- 官网 `index.html` / `document.html` 中「发布稿」字样与依赖提示

## 3. 错误处理

- 沿用 `_http.py` WZWS 检测/重试；节点无数据抛 `RuntimeError('NBS <cid> 无数据')`（不返回空表）
- 1—2 月合并发布语义在 API 侧天然一致（归月 02），无需特殊处理

## 4. 测试（强制）

1. 单元（离线）：三接口 metric 非法值校验移除后，新增 cid 映射存在性测试（11+3+1 节点常量表）；宽表组装测试沿用 `to_wide_frame`
2. 实跑（联网）：三接口 `start_month='2026-08'` 返回 3 行非空宽表、首列月份、最新月 2026-10；`fn().head(1)` 确认各接口最早月并固化到 docstring/官网
3. 全量回归：16 接口样例脚本重跑（`tools/gen-doc-samples.py --only china`），官网 `document-samples.js` 更新
4. 视觉/交互：官网两页数字与文案更新后浏览器走查（卡片数据来源/深度年份、文档页三接口参数表无 metric/max_reports、调用结果为新数据）

## 5. 交付物

- 包：三接口重写 + `_report.py` 删除 + catalog/docstring 更新 + 测试更新
- 官网：`index.html`（CARDS 中国三卡数字/来源）、`document.html`（I 对象三接口：sig 无 metric、desc、deps 改「无额外依赖」、outputs 列数、cov）、`document-samples.js` 重生成
- 容器重建上线 + 两仓库推送 GitHub + 知识库决策/教训更新
