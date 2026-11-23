"""deepmacro — 宏观数据接口包（中国/美国/日本/韩国/台湾）。

用法与 akshare 同风格：函数式调用、返回 pandas DataFrame、无本地存储。

    >>> import deepmacro as dm
    >>> dm.macro_china_cpi(start_month='2024-01').tail(3)
    >>> dm.catalog()   # 全部可用数据接口目录
"""
from deepmacro.china.cpi import macro_china_cpi
from deepmacro.china.fai import macro_china_fai
from deepmacro.china.iva import macro_china_iva
from deepmacro.china.pmi import macro_china_pmi
from deepmacro.china.ppi import macro_china_ppi
from deepmacro.china.retail import macro_china_retail
from deepmacro.japan.cpi import macro_japan_cpi
from deepmacro.japan.ppi import macro_japan_ppi
from deepmacro.korea.cpi import macro_korea_cpi
from deepmacro.korea.iip import macro_korea_iip
from deepmacro.korea.ppi import macro_korea_ppi
from deepmacro.taiwan.cpi import macro_taiwan_cpi
from deepmacro.taiwan.ppi import macro_taiwan_ppi
from deepmacro.us.cci import macro_us_cci
from deepmacro.us.cpi import macro_us_cpi
from deepmacro.us.ppi import macro_us_ppi

__version__ = '0.1.0'

__all__ = [
    '__version__',
    'catalog',
    # 中国
    'macro_china_cpi', 'macro_china_ppi', 'macro_china_iva',
    'macro_china_pmi', 'macro_china_retail', 'macro_china_fai',
    # 美国
    'macro_us_cpi', 'macro_us_ppi', 'macro_us_cci',
    # 日本
    'macro_japan_cpi', 'macro_japan_ppi',
    # 韩国
    'macro_korea_cpi', 'macro_korea_ppi', 'macro_korea_iip',
    # 台湾
    'macro_taiwan_cpi', 'macro_taiwan_ppi',
]

# 数据接口目录：函数名 → (地区, 指标, 数据源, 官方链接)
_CATALOG: dict[str, tuple[str, str, str, str]] = {
    'macro_china_cpi': ('中国', '居民消费价格指数（CPI）',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
    'macro_china_ppi': ('中国', '工业生产者价格指数（PPI）',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
    'macro_china_iva': ('中国', '规模以上工业增加值',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
    'macro_china_pmi': ('中国', '采购经理指数（PMI）',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
    'macro_china_retail': ('中国', '社会消费品零售总额（总量 8 指标）',
                           '国家统计局数据发布库 API',
                           'https://data.stats.gov.cn/'),
    'macro_china_fai': ('中国', '固定资产投资（不含农户，全科目）',
                        '国家统计局数据发布库 API',
                        'https://data.stats.gov.cn/'),
    'macro_us_cpi': ('美国', 'CPI（19 科目指数水平）',
                     'BLS API / FRED 镜像',
                     'https://www.bls.gov/cpi/'),
    'macro_us_ppi': ('美国', 'PPI（16 科目指数水平）',
                     'BLS API / FRED 镜像',
                     'https://www.bls.gov/ppi/'),
    'macro_us_cci': ('美国', '密歇根大学消费者信心指数',
                     'FRED（UMCSENT）',
                     'https://fred.stlouisfed.org/series/UMCSENT'),
    'macro_japan_cpi': ('日本', '消費者物価指数（CPI，中分類+品目別）',
                        'e-Stat 総務省統計局',
                        'https://www.e-stat.go.jp/'),
    'macro_japan_ppi': ('日本', '国内企業物価指数（PPI）',
                        '日本銀行',
                        'https://www.stat-search.boj.or.jp/'),
    'macro_korea_cpi': ('韩国', '消费者物价指数（CPI，지출목적별全科目）',
                        'KOSIS 统计厅（需 deepmacro[korea]）',
                        'https://kosis.kr/'),
    'macro_korea_ppi': ('韩国', '生产者物价指数（PPI，기본분류 425 科目）',
                        'KOSIS 韩国银行（需 deepmacro[korea]）',
                        'https://kosis.kr/'),
    'macro_korea_iip': ('韩国', '矿业工业生产指数（112 산업）',
                        'KOSIS 统计厅（需 deepmacro[korea]）',
                        'https://kosis.kr/'),
    'macro_taiwan_cpi': ('台湾', '消費者物價指數（CPI，449 序列）',
                         '主計總處 nstatdb',
                         'https://nstatdb.dgbas.gov.tw/'),
    'macro_taiwan_ppi': ('台湾', '生產者物價指數（PPI，101 序列）',
                         '主計總處 nstatdb',
                         'https://nstatdb.dgbas.gov.tw/'),
}


def catalog():
    """全部可用数据接口目录 → DataFrame[接口, 地区, 指标, 数据源, 官方链接]。"""
    import pandas as pd
    return pd.DataFrame(
        [{'接口': name, '地区': region, '指标': indicator,
          '数据源': source, '官方链接': url}
         for name, (region, indicator, source, url) in _CATALOG.items()])
