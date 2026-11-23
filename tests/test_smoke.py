"""离线冒烟测试 — 不触网：目录完整性、宽表组装、解析器单测。"""
from __future__ import annotations

import pandas as pd
import pytest

import deepmacro as dm
from deepmacro._frame import to_wide_frame
from deepmacro.china import _nbs
from deepmacro.china._report import extract_month_from_title
from deepmacro.japan._sources import parse_cgpi_csv
from deepmacro.korea._parse import _extend_start, parse_kosis_ppi_csv
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


def test_fai_node_table():
    """FAI NBS 节点表完整（10 个有效节点，2004-11 空节点已剔除）。"""
    from deepmacro.china import fai
    assert len(fai._FAI_NODES) == 10
    segs = sorted((s, e) for (_l, s, e, _c) in fai._FAI_NODES if '行业' in _l)
    assert segs[0][0] <= '1998-01'
    for (_s, prev_end), (next_start, _e) in zip(segs, segs[1:]):
        assert prev_end < next_start  # 年代分段互不重叠


def test_fai_clean_name():
    from deepmacro.china.fai import _clean_name
    assert _clean_name('固定资产投资额累计增长 (%)') == '固定资产投资额累计增长'
    assert _clean_name('民间固定资产投资累计增长 (%)') == '民间固定资产投资累计增长'
    assert _clean_name('民间固定资产投资累计增长（%）') == '民间固定资产投资累计增长'


def test_fai_core_node_hard_failure(monkeypatch):
    """概况节点失败应硬抛 RuntimeError，其余节点维持软降级。"""
    import deepmacro.china.fai as fai

    def boom(cid, period_range, result):
        raise ConnectionError('WZWS challenge')

    monkeypatch.setattr(fai, '_query_node', boom)
    with pytest.raises(RuntimeError, match='概况'):
        fai.macro_china_fai(start_month='2024-06', end_month='2024-06')


def test_pmi_nodes_and_prefix():
    from deepmacro.china import pmi
    assert set(pmi._PMI_NODES) == {'制造业', '非制造业', '综合'}
    assert pmi._CORE_GROUP == '制造业'
    # 缓存为空时（离线默认态）不加前缀
    assert pmi._prefixed('制造业', '新订单指数') == '新订单指数'
    assert pmi._prefixed('综合', '综合PMI产出指数') == '综合PMI产出指数'


def test_pmi_dup_cache(monkeypatch):
    from deepmacro.china import pmi
    monkeypatch.setattr(pmi, '_DUP_CACHE', {'新订单指数', '新出口订单指数'})
    assert pmi._prefixed('制造业', '新订单指数') == '制造业·新订单指数'
    assert pmi._prefixed('非制造业', '新出口订单指数') == '非制造业·新出口订单指数'
    assert pmi._prefixed('制造业', '生产指数') == '生产指数'


def test_pmi_core_node_hard_failure(monkeypatch):
    """制造业节点失败应硬抛 RuntimeError，其余节点维持软降级。"""
    import deepmacro.china.pmi as pmi

    monkeypatch.setattr(pmi, '_refresh_dup_names',
                        lambda: set(pmi._PMI_NODES))

    def boom(group, cid, period_range, result):
        raise ConnectionError('WZWS challenge')

    monkeypatch.setattr(pmi, '_query_node', boom)
    with pytest.raises(RuntimeError, match='制造业'):
        pmi.macro_china_pmi(start_month='2024-06', end_month='2024-06')


def test_pmi_soft_group_enum_skip(monkeypatch):
    """非制造业枚举失败 → 不进成功集合、不参与同名统计；主循环跳过其取数。"""
    import deepmacro.china.pmi as pmi
    from deepmacro.china import _nbs

    def fake_indicators(cid, refresh=False):
        if cid == pmi._PMI_NODES['非制造业']:
            raise ConnectionError('WZWS challenge')
        if cid == pmi._PMI_NODES['制造业']:
            return [{'iid': 'm1', 'name': '新订单指数'},
                    {'iid': 'm2', 'name': '生产指数'}]
        return [{'iid': 'c1', 'name': '综合PMI产出指数'}]

    monkeypatch.setattr(_nbs, 'query_indicators', fake_indicators)
    monkeypatch.setattr(pmi, '_DUP_CACHE', pmi._DUP_CACHE)  # 锁定，结束自动还原

    ok = pmi._refresh_dup_names()
    assert ok == {'制造业', '综合'}
    assert pmi._DUP_CACHE == set()  # 新订单指数仅一组成功计入，不构成同名

    called: list[str] = []

    def fake_query(group, cid, period_range, result):
        called.append(group)
        result.setdefault('2024-06', {})[f'{group}·有数'] = 50.0

    monkeypatch.setattr(pmi, '_query_node', fake_query)
    df = pmi.macro_china_pmi(start_month='2024-06', end_month='2024-06')
    assert called == ['制造业', '综合']  # 枚举失败组不取数
    assert '非制造业·有数' not in df.columns
    assert df['制造业·有数'].tolist() == [50.0]


def test_parse_cgpi_csv():
    csv_text = (
        ',,,202401,202402\n'
        'PRCG20_11,国内企業物価指数,[国内企業物価指数] 総平均,120.0,120.5\n'
        'PRCG20_12,統計名,品目/____処理牛乳,100.0,\n'
    )
    out = parse_cgpi_csv(csv_text)
    assert out['総平均'] == {'2024-01': 120.0, '2024-02': 120.5}
    assert out['処理牛乳'] == {'2024-01': 100.0}


def test_extend_start_metrics():
    """_extend_start：yoy 前移 12 月 / mom 前移 1 月 / level 不变 / 跨年回退。"""
    assert _extend_start('2026-08', 'yoy') == '2025-08'
    assert _extend_start('2026-08', 'mom') == '2026-07'
    assert _extend_start('2026-08', 'level') == '2026-08'
    assert _extend_start('2018-02', 'yoy') == '2017-02'
    assert _extend_start('2018-01', 'yoy') == '2017-01'
    assert _extend_start('2018-03', 'mom') == '2018-02'


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


def test_korea_iip_yoy_no_base_raises(monkeypatch):
    """iip yoy 窗口落在最早年、基期缺失时应抛 RuntimeError 而非静默空表。"""
    import deepmacro.korea.iip as iip_mod
    monkeypatch.setattr(iip_mod, 'fetch_korea_iip', lambda start, end: {
        'level': {'총지수': {'2018-01': 100.0}},
        'level_sa': {},
    })
    with pytest.raises(RuntimeError):
        iip_mod.macro_korea_iip(start_month='2018-01', metric='yoy')


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


def test_retail_node():
    from deepmacro.china import retail
    assert retail._RETAIL_CID == 'd0cb882c7f27443ab6b3ef9421901961'


def test_retail_clean_name():
    from deepmacro.china.retail import _clean_name
    assert _clean_name('社会消费品零售总额当期值 (亿元)') == '社会消费品零售总额当期值'
    assert _clean_name('社会消费品零售总额同比增长 (%)') == '社会消费品零售总额同比增长'
    assert _clean_name('限上单位消费品零售额当期值 (亿元)') == '限上单位消费品零售额当期值'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
