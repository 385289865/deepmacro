# 设计文档 — deepmacro v0.1.0（宏观数据接口包 + 数据目录站）

日期：2026-11-22
状态：**方案已经用户逐项确认**（韩国源/网站形态/仓库可见性/spec 入库 四项裁决见 §1.2）
上游：factorlab `backend/macro/` 生产验证的接口逆向成果（探测记录见其
`docs/superpowers/specs/2026-11-10-jpkr-tw-official-breakdown-design.md` 等）

---

## 1. 范围与裁决

### 1.1 目标

- 新建 **deepmacro**：类似 akshare 的宏观数据接口包，pip 可安装，覆盖
  中美日韩台，核心为中国国家统计局接口解析（工增/社零等）。
- 新建 **deepmacro-website**：数据目录站，参考 tushare 页面样式，
  告诉用户如何通过包拿数据。

### 1.2 用户裁决（2026-11-22 会话确认）

1. 韩国数据源：~~FRED 镜像~~ → **实测 FRED 的 OECD 韩国系列已停更**
   （CPI 止于 2023-11、能源/核心止于 2025-04、食品序列 404），改裁为
   **KOSIS 官方 + 可选依赖**（移植 factorlab kosis_client Playwright 管线，
   `pip install "deepmacro[korea]"`）。
2. 网站形态：**参考 tushare 当前页面样式与实现**，静态简单。
3. 仓库：**deepmacro 公开**（pip 包），**deepmacro-website 私有**。
4. 本 spec 按惯例写入 `docs/specs/`。

### 1.3 不在本版（roadmap）

- 美国 PMI（ISM WAF + PRNewswire 归档链路，后续版本）
- 中国社零/固投的深史（NBS API cid 发现后可扩展；当前为发布稿窗口）
- 汇率/利率/货币供应/房地产等 factorlab 其余 40+ 爬虫族
- PyPI 发布（GitHub 公开后另行 CI）

### 1.4 实测记录（2026-11-22，大陆网络直连）

- 中国 6 接口全部通过（CPI/PPI 50 列、IVA 50 列、PMI 单报告 14 月 30 列、
  社零 26 科目、固投全科目，数据至 2026-10）。
- 日本 PPI 通过（BOJ zip 905 系列）；日本 CPI e-Stat 接口连通验证。
- 美国 CPI 经 FRED 降级通过（BLS 直连被 Akamai 拦截；配代理后 BLS 全量）；
  CCI 通过。
- **FRED 的 Akamai 拦截自定义 bot UA（HTTP 000 连接重置）** → 客户端
  不设自定义 UA（requests 默认 python-requests/x.y 实测可用）。
- 台湾 nstatdb 403（设计如此，需代理）；韩国 KOSIS 需浏览器会话（可选依赖）。

## 2. 架构

```
deepmacro/                  # 仓库名 = 包名
├── pyproject.toml          # hatchling；依赖 requests/pandas/bs4/lxml/openpyxl
├── docs/specs/             # 本文档
├── deepmacro/
│   ├── __init__.py         # 全部公开函数 + catalog()
│   ├── _http.py            # 会话/WZWS 检测/重试/代理（DEEPMACRO_HTTP_PROXY）
│   ├── _frame.py           # {月份:{科目:值}} → 宽表 DataFrame
│   ├── china/              # _nbs.py（数据发布库 API）+ _report.py（发布稿扫描）
│   │   └── cpi/ppi/iva/pmi/retail/fai
│   ├── us/                 # _sources.py（BLS+FRED 客户端）+ cpi/ppi/cci
│   ├── japan/              # _sources.py（e-Stat/BOJ）+ cpi/ppi
│   ├── korea/              # _kosis.py（Playwright 客户端，可选依赖）+ _parse.py + cpi/ppi/iip
│   └── taiwan/             # _nstatdb.py（cookie 种植客户端）+ cpi/ppi
└── tests/                  # 离线冒烟（catalog/宽表组装/解析器单测）
```

**设计原则**（与 factorlab 的差异）：

1. **无状态**：不落库（factorlab 存 DuckDB），每次调用现取，akshare 语义。
2. **代理统一**：`DEEPMACRO_HTTP_PROXY` 环境变量（factorlab 的
   net_client/local_config 换成纯环境变量），BLS/e-Stat/nstatdb 必须。
3. **返回宽表**：月份 + 科目列（akshare 风格）；社零例外（长表，
   因 26 科目 × 5 口径宽表过宽）。
4. **cid 复用**：CPI/PPI/IVA 的 NBS cid 表与科目名映射逐字复用自
   factorlab（生产验证 2026-10 校准）。

## 3. 数据源矩阵（v0.1.0，16 接口）

| 接口 | 地区 | 指标 | 源 | 方式 | 历史 | 网络 |
|---|---|---|---|---|---|---|
| macro_china_cpi | 中国 | CPI 全科目（yoy/mom/cumu） | data.stats.gov.cn /stream/esData | cid 按基期切片（2011/16/21/26） | 2011 起（可回溯） | 直连 |
| macro_china_ppi | 中国 | PPI 49 科目 | 同上 | 出厂+购进+行业分片 | 2007/2011 起 | 直连 |
| macro_china_iva | 中国 | 工业增加值全科目（yoy/cumu） | 同上 | 6 节点（主指标/经济类型/门类/行业×3 段） | 2003/2010 起 | 直连 |
| macro_china_pmi | 中国 | PMI+13 分项+非制造业+衍生 | stats.gov.cn 发布稿 | zxfbhjd+zxfb 索引扫描，表内 13 月历史合并 | 近 3 报告×13 月 | 直连 |
| macro_china_retail | 中国 | 社零 26 科目 5 口径（长表） | 发布稿 | 主附表+环比表（1—2 月合并稿 3 列变体） | 近 3 报告 | 直连 |
| macro_china_fai | 中国 | 固投全科目（累计同比） | 发布稿 | 附表路由+正文核心口径 | 近 3 报告 | 直连 |
| macro_us_cpi | 美国 | CPI 19 科目水平（SA） | BLS API v2 | 25 序列/次分块；失败 FRED 降级 | 参数化（默认 5 年） | 代理 |
| macro_us_ppi | 美国 | PPI 12 BLS+4 FRED | 同上 | 同上 | 同上 | 代理 |
| macro_us_cci | 美国 | 密歇根消费者信心 | FRED UMCSENT | fredgraph.csv（限速退避） | 1952 起 | 直连/代理 |
| macro_japan_cpi | 日本 | CPI 中分類+品目別 | e-Stat 00200573 | 4 步下钻动态 statInfId → xlsx | 1970 起（当期文件全史） | 代理 |
| macro_japan_ppi | 日本 | 企業物価指數 3042 系列 | BOJ cgpi_m_jp.zip | 直链+shift_jis CSV | 2020 起 | 直连/代理 |
| macro_korea_cpi | 韩国 | CPI 지출목적별全科目（2020=100） | KOSIS DT_1J22001 | Playwright 管线（deepmacro[korea]），按年分片 | 2013 起 | 建议代理 |
| macro_korea_ppi | 韩国 | PPI 기본분류 526 科目 | KOSIS DT_404Y014（BOK） | 同上 | 2013 起 | 同上 |
| macro_korea_iip | 韩国 | 광공업생산지수 115 산업（원지수+季调） | KOSIS DT_1F02001 | 同上（item T10/T20） | 2018 起 | 同上 |
| macro_taiwan_cpi | 台湾 | CPI 449 序列 | nstatdb A030101025 | cookie 种植+sys=220 矩阵 | 1981 起 | **必须代理** |
| macro_taiwan_ppi | 台湾 | PPI 101 序列 | nstatdb A030701015 | 同上 | 2021 起 | **必须代理** |

## 4. 接口约定

- 签名统一：`(start_month=None, end_month=None, metric='yoy', …)`，
  月份 'YYYY-MM' 字符串比较裁剪。
- 输出：pandas 宽表（首列 `月份`，科目列按官方体系自然序）；社零为长表
  `[月份, 科目, 当月（亿元）, 当月同比（%）, 累计（亿元）, 累计同比（%）]`。
- 水平值源（日/台/美/韩）支持 `metric='level'|'yoy'|'mom'`（yoy/mom 由
  水平派生，与 factorlab §4.1 口径一致）；中国 CPI/PPI/IVA 为官方原生增速。
- 失败语义：抛 RuntimeError（附网络/代理提示），不返回空 DataFrame。

## 5. 网站设计（deepmacro-website，私有）

- 参考 tushare 当前页面样式与实现：深色导航 + hero（pip install 命令 +
  复制按钮）+ 特性卡片（中美日韩台五地区）+ 数据目录表（16 接口）+
  快速开始代码块 + footer。
- 纯静态 `index.html`（内嵌 CSS/JS，零构建零依赖），GitHub Pages 可托管。
- 目录数据与包内 `catalog()` 一致（人工同步，接口稳定后可脚本生成）。

## 6. 发布

- deepmacro 公开仓；deepmacro-website 私有仓。
- 版本 0.1.0；PyPI 发布与 CI 留待后续版本。
