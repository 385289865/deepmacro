# deepmacro

宏观数据接口包 —— 覆盖 **中国 / 美国 / 日本 / 韩国 / 台湾** 的宏观指标。
类似 akshare 的用法：函数式调用、返回 pandas DataFrame、无本地存储、数据全部来自各国官方统计机构接口。

## 安装

```bash
pip install deepmacro
```

## 快速开始

```python
import deepmacro as dm

# 中国 CPI（国家统计局官方接口，全量科目，2011 年起）
df = dm.macro_china_cpi(start_month="2024-01")
print(df.tail())

# 工业增加值 / 社零 / 固投 / PMI
dm.macro_china_iva()
dm.macro_china_retail()
dm.macro_china_fai()
dm.macro_china_pmi()

# 海外
dm.macro_us_cpi()
dm.macro_japan_cpi()
dm.macro_korea_cpi()
dm.macro_taiwan_cpi()

# 全部可用接口目录
dm.catalog()
```

## 数据目录（v0.1.0，16 个接口）

| 接口 | 地区 | 指标 | 数据源 |
|---|---|---|---|
| `macro_china_cpi` | 中国 | 居民消费价格指数（全科目） | 国家统计局数据发布库 API |
| `macro_china_ppi` | 中国 | 工业生产者价格指数（49 科目） | 国家统计局数据发布库 API |
| `macro_china_iva` | 中国 | 规模以上工业增加值（全科目） | 国家统计局数据发布库 API |
| `macro_china_pmi` | 中国 | 采购经理指数（制造业+非制造业+衍生） | 国家统计局发布稿 |
| `macro_china_retail` | 中国 | 社会消费品零售总额（26 科目×5 口径） | 国家统计局发布稿 |
| `macro_china_fai` | 中国 | 固定资产投资（全科目） | 国家统计局发布稿 |
| `macro_us_cpi` | 美国 | CPI（19 科目指数水平） | BLS API / FRED |
| `macro_us_ppi` | 美国 | PPI（16 科目指数水平） | BLS API / FRED |
| `macro_us_cci` | 美国 | 密歇根大学消费者信心指数 | FRED |
| `macro_japan_cpi` | 日本 | 消費者物価指数（中分類+品目別） | e-Stat 总务省统计局 |
| `macro_japan_ppi` | 日本 | 国内企業物価指数 | 日本银行 |
| `macro_korea_cpi` | 韩国 | 消费者物价指数（지출목적별全科目） | KOSIS 统计厅* |
| `macro_korea_ppi` | 韩国 | 生产者物价指数（기본분류 526 科目） | KOSIS 韩国银行* |
| `macro_korea_iip` | 韩国 | 矿业工业生产指数（115 산업） | KOSIS 统计厅* |
| `macro_taiwan_cpi` | 台湾 | 消費者物價指數（449 序列） | 主计总处 nstatdb |
| `macro_taiwan_ppi` | 台湾 | 生產者物價指數（101 序列） | 主计总处 nstatdb |

运行 `dm.catalog()` 获取同样内容的 DataFrame。带 * 的韩国接口为可选依赖（见下文「韩国官方数据」）。

## 接口约定

- 统一签名：`func(start_month=None, end_month=None, metric='yoy')`，月份格式 `'YYYY-MM'`。
- 返回宽表 DataFrame（首列 `月份`，其余为科目列）；`macro_china_retail` 为长表（26 科目 × 5 口径）。
- `metric`：`'yoy'` 同比 | `'mom'` 环比 | `'cumulative'` 累计同比（中国）；海外水平值源另支持 `'level'` 指数原值。
- 中国 1—2 月数据官方合并发布（归月 02），每年 1 月无数据。

## 网络与代理

中国官方接口直连即可；部分海外源在中国大陆需要代理：

```bash
export DEEPMACRO_HTTP_PROXY=socks5://127.0.0.1:1080   # 或 http://...
```

| 数据源 | 中国大陆直连 | 说明 |
|---|---|---|
| 国家统计局（data.stats.gov.cn / stats.gov.cn） | 可用 | 高频请求会触发 WZWS 反爬，内置重试 |
| FRED | 基本可用 | 内置限速 |
| BLS | 通常被拦 | 需代理；失败自动降级 FRED |
| e-Stat（日本） | 通常不可达 | 需代理 |
| KOSIS（韩国） | 限速 | 建议代理；KOSIS 按浏览器会话 IP 限速 |
| nstatdb（台湾） | 403（Cloudflare） | **必须代理** |

可选环境变量 `BLS_API_KEY`（https://api.bls.gov/developers/ 免费注册）提升 BLS 配额。

## 韩国官方数据（可选依赖）

韩国 KOSIS 官方接口对纯 requests 稳定拦截（WAF），需要真实浏览器会话：

```bash
pip install "deepmacro[korea]"
playwright install chromium
```

未安装时调用韩国接口会抛出带安装指引的 RuntimeError。

## 设计文档

- [docs/specs/2026-11-22-deepmacro-v0.1.0-design.md](docs/specs/2026-11-22-deepmacro-v0.1.0-design.md)

## Roadmap

- 美国 PMI（ISM）
- 韩国 KOSIS 官方全细分科目（可选依赖方案）
- 中国社零/固投深史（NBS 数据发布库 cid 扩展）
- 利率、汇率、货币供应、房地产等更多指标族

## License

MIT
