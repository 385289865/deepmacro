"""离线冒烟测试 — 不触网：目录完整性、宽表组装、解析器单测。"""
from __future__ import annotations

import pandas as pd
import pytest

import deepmacro as dm
from deepmacro._frame import to_wide_frame
from deepmacro.china import _nbs
from deepmacro.china._report import extract_month_from_title
from deepmacro.japan._sources import parse_cgpi_csv
from deepmacro.korea._parse import parse_kosis_ppi_csv
from deepmacro.taiwan._nstatdb import (
    month_to_roc_ym, parse_nstatdb_matrix, roc_ym_to_month,
)


def test_catalog_matches_exports():
    """catalog 表覆盖 __all__ 全部接口函数。"""
    df = dm.catalog()
    assert list(df.columns) == ['接口', '地区', '指标', '数据源', '官方链接']
    exported = [n for n in dm.__all__
                if n not in ('__version__', 'catalog')]
    assert sorted(df['接口'].tolist()) == sorted(exported)
    assert len(df) == 16
    # 每个接口函数真实存在且可调用
    for name in df['接口']:
        assert callable(getattr(dm, name, None)), name


def test_regions_coverage():
    df = dm.catalog()
    assert set(df['地区']) == {'中国', '美国', '日本', '韩国', '台湾'}


def test_to_wide_frame():
    by_month = {
        '2024-02': {'全国': -0.7, '食品': 2.0},
        '2024-01': {'全国': -0.8},
    }
    df = to_wide_frame(by_month)
    assert df['月份'].tolist() == ['2024-01', '2024-02']
    assert df['全国'].tolist() == [-0.8, -0.7]
    assert pd.isna(df.loc[0, '食品']) and df.loc[1, '食品'] == 2.0


def test_nbs_period_converters():
    assert _nbs.month_to_period('2025-03') == '202503MM'
    assert _nbs.period_to_month('202503MM') == '2025-03'
    assert _nbs.detect_base_period('2026-05') == '2026'
    assert _nbs.detect_base_period('2023-05') == '2021'
    assert _nbs.detect_base_period('2018-05') == '2016'
    assert _nbs.detect_base_period('2013-05') == '2011'


def test_extract_month_from_title():
    assert extract_month_from_title('2026年1—9月份社会消费品零售总额增长1.1%') == '2026-09'
    assert extract_month_from_title('2026年9月份规模以上工业增加值增长5.5%') == '2026-09'
    assert extract_month_from_title('2026年6月中国采购经理指数运行情况') == '2026-06'


def test_parse_cgpi_csv():
    csv_text = (
        ',,,202401,202402\n'
        'PRCG20_11,国内企業物価指数,[国内企業物価指数] 総平均,120.0,120.5\n'
        'PRCG20_12,統計名,品目/____処理牛乳,100.0,\n'
    )
    out = parse_cgpi_csv(csv_text)
    assert out['総平均'] == {'2024-01': 120.0, '2024-02': 120.5}
    assert out['処理牛乳'] == {'2024-01': 100.0}


def test_parse_kosis_ppi_csv():
    """KOSIS DT_404Y014（생산자물가지수）宽表：无 시도 轴，列 0 = 科目标签。"""
    csv_text = (
        '13101134604ACC_CD 계정코드별,'
        'M202610 M202610 2026.10 p),M202609 2026.09,M202608 2026.08\n'
        '13102134604ACC_CD.*AA 총지수 (14999301.814 2020=100),'
        '129.66,129.76,129.58\n'
        '13102134604ACC_CD.1AA 농림수산품 (14999301.814 2020=100),'
        '117.54,128.18,125.50\n'
        '13102134604ACC_CD.1011AA 농산물 (14999301.814 2020=100),'
        '103.85,117.81,114.87\n'
    )
    out = parse_kosis_ppi_csv(csv_text)
    assert out['총지수'] == {'2026-10': 129.66, '2026-09': 129.76,
                            '2026-08': 129.58}
    assert out['농림수산품'] == {'2026-10': 117.54, '2026-09': 128.18,
                                '2026-08': 125.50}
    assert out['농산물'] == {'2026-10': 103.85, '2026-09': 117.81,
                            '2026-08': 114.87}


def test_korea_iip_sa_metric(monkeypatch):
    """iip 的 'sa' metric 应映射到 fetch 返回的 'level_sa' 键。"""
    import deepmacro.korea.iip as iip_mod
    monkeypatch.setattr(iip_mod, 'fetch_korea_iip', lambda start, end: {
        'level': {'총지수': {'2026-08': 110.0}},
        'level_sa': {'총지수': {'2026-08': 115.0}},
    })
    assert iip_mod.macro_korea_iip(start_month='2026-08', metric='sa')['총지수'].tolist() == [115.0]
    assert iip_mod.macro_korea_iip(start_month='2026-08', metric='level')['총지수'].tolist() == [110.0]


def test_roc_month_roundtrip():
    assert roc_ym_to_month(11510) == '2026-10'
    assert month_to_roc_ym('2026-10') == 11510
    assert roc_ym_to_month('115年10月') == '2026-10'
    assert roc_ym_to_month('70年') is None       # 年度行
    assert roc_ym_to_month(7000) is None          # 月 0


def test_parse_nstatdb_matrix():
    payload = {
        'funid': 'TEST',
        'nrow': [['115年9月', ''], ['115年10月', ''], ['70年', '']],
        'ncol': [['總指數', '', ''], ['一.食物類', '', '']],
        'orgdata': [[105.1, 105.2, 100.0], [103.0, 0, 100.0]],
    }
    series = parse_nstatdb_matrix(payload)
    assert series['總指數'] == {'2026-09': 105.1, '2026-10': 105.2}
    assert series['一.食物類'] == {'2026-09': 103.0}  # 0 = 无数据剔除


@pytest.mark.parametrize('func', [dm.macro_china_cpi, dm.macro_china_ppi,
                                  dm.macro_china_iva, dm.macro_japan_cpi,
                                  dm.macro_taiwan_cpi, dm.macro_korea_cpi])
def test_metric_validation(func):
    with pytest.raises(ValueError):
        func(metric='bogus')


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
