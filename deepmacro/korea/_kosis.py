"""KOSIS（韩国统计厅国家统计门户）Playwright 采集客户端。

移植自 factorlab backend/macro/kosis_client.py（2026-11-10 探测定稿）。

管线：
  1. 预热 GET kosis.kr（浏览器会话 + WAF 放行）
  2. statHtml.do viewer 壳 → 原生 JS 加载 content iframe（statHtmlContent.do）
     并初始化分类 fancytree
  3. 从 content frame 提取：g_jsonStatInfo / ParamInfo 表单 /
     defaultRowList·defaultColList / fancytree 全量分类树
  4. 构造 fieldList（PRD 月份 + ITM_ID + OV_L{sn}_ID）
  5. frame 上下文 fetch POST downGrid.do → {"file": token}
  6. POST downNormal.do → CSV

WAF 实测：OLAP 端点对纯 requests 稳定 500；KOSIS 按 IP 对 Chromium 会话
限速（tarpit）→ 可设 ``DEEPMACRO_HTTP_PROXY`` 走代理出口。常规下载上限
1 万格（>1 万需分片，调用方按年分片）。

可选依赖：``pip install "deepmacro[korea]"`` 且首次使用前执行
``playwright install chromium``。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

_log = logging.getLogger(__name__)

KOSIS_BASE = 'https://kosis.kr'
KOSIS_SOURCE_URL = 'https://kosis.kr/'
STATHTML_URL = KOSIS_BASE + '/statHtml/statHtml.do'

_FORM_RE = re.compile(r'<form[^>]*name="ParamInfo"[^>]*>(.*?)</form>', re.S)
_INPUT_RE = re.compile(r'<input[^>]*>')
_SELECT_RE = re.compile(
    r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', re.S)
_JSON_RE = re.compile(r"g_jsonStatInfo\s*=\s*'(.*?)';", re.S)
_ROW_RE = re.compile(r'defaultRowList\.push\("([^"]+)"\)')
_COL_RE = re.compile(r'defaultColList\.push\("([^"]+)"\)')


def _proxy_url() -> str | None:
    """DEEPMACRO_HTTP_PROXY → Playwright 代理 URL；未设置返回 None（直连）。"""
    import os
    url = os.environ.get('DEEPMACRO_HTTP_PROXY', '').strip()
    if not url:
        return None
    # Chromium 不识别 socks5h://（远端 DNS 由浏览器实现兜底）
    return url.replace('socks5h://', 'socks5://', 1)


def build_order_str(axes: list[dict[str, Any]], row_axis: str,
                    col_axis: str) -> str:
    """orderStr 构造：TIME→TIME / ITEM→CHAR_ITM_ID / 分类→OV_L{sn}_ID。"""
    sn_map = {str(ci.get('classId')): str(ci.get('sn')) for ci in axes}
    toks: list[str] = []
    for axis in (row_axis, col_axis):
        for tok in axis.split(','):
            if tok in ('TIME', 'TIME_YEAR', 'TIME_MQ'):
                toks.append('TIME')
            elif tok == 'ITEM':
                toks.append('CHAR_ITM_ID')
            else:
                toks.append(f"OV_L{sn_map[tok]}_ID")
    return ','.join(toks)


def flatten_tree(nodes: list[dict[str, Any]],
                 out: list[dict[str, Any]] | None = None
                 ) -> list[dict[str, Any]]:
    """fancytree dump → 平铺节点列表（{id, lvl, name, up, leaf}）。"""
    out = [] if out is None else out
    for n in nodes or []:
        d = n.get('data') or {}
        out.append({
            'id': d.get('itmId') or n.get('key'),
            'lvl': d.get('lvl'),
            'name': d.get('scrKor') or n.get('title'),
            'up': d.get('upItmId'),
            'leaf': d.get('leaf'),
        })
        flatten_tree(n.get('children') or [], out)
    return out


def build_field_list(stat_info: dict[str, Any], months: list[str],
                     item_ids: list[str] | None = None,
                     class_ids: dict[str, list[str]] | None = None,
                     class_trees: dict[str, list[dict[str, Any]]] | None = None
                     ) -> list[dict[str, str]]:
    """fieldList 构造（原生 fn_searchCond 格式）。

    months：'YYYYMM' 列表；item_ids：项目轴 id（None → defaultItmList 全部）；
    class_ids：{sn: [ids]} 按轴覆盖；class_trees：{sn: fancytree dump}
    （给定时未覆盖的轴用全量 id）。
    """
    fl: list[dict[str, str]] = [{
        'targetId': 'PRD', 'targetValue': '',
        'prdValue': 'M,' + ','.join(months) + ',@'}]
    ids = item_ids or [str(x) for x in
                       (stat_info.get('itemInfo') or {}).get('defaultItmList')
                       or []]
    for iid in ids:
        fl.append({'targetId': 'ITM_ID', 'targetValue': str(iid),
                   'prdValue': ''})
    class_ids = class_ids or {}
    class_trees = class_trees or {}
    for ci in stat_info.get('classInfoList') or []:
        sn = str(ci.get('sn'))
        if sn in class_ids:
            cids = [str(x) for x in class_ids[sn]]
        elif sn in class_trees:
            cids = [n['id'] for n in flatten_tree(class_trees[sn])]
        else:
            cids = [str(s.split('#')[1]) for s in ci.get('defaultItmList')
                    or []]
        for cid in cids:
            fl.append({'targetId': f'OV_L{sn}_ID', 'targetValue': cid,
                       'prdValue': ''})
    return fl


_JS_STAT_INFO = """() => {
    const html = document.documentElement.outerHTML;
    const m = html.match(/g_jsonStatInfo\\s*=\\s*'(.*?)';/s);
    if (!m) return null;
    return JSON.parse(m[1]);
}"""

_JS_TREE_READY = """(sns) => {
    if (typeof $ === 'undefined' || !$.ui || !$.ui.fancytree) return false;
    for (const sn of sns) {
        try {
            const t = $.ui.fancytree.getTree('#fancytree_' + sn);
            if (!t || !t.getRootNode().children
                    || !t.getRootNode().children.length) return false;
        } catch (e) { return false; }
    }
    return true;
}"""

_JS_DUMP_FRAME = """() => {
    const html = document.documentElement.outerHTML;
    const js = JSON.parse(html.match(/g_jsonStatInfo\\s*=\\s*'(.*?)';/s)[1]);
    const h0 = html.indexOf('var defaultRowList');
    const seg = html.substring(h0, h0 + 900);
    const rows = [...seg.matchAll(/defaultRowList\\.push\\("([^"]+)"\\)/g)]
        .map(m => m[1]);
    const cols = [...seg.matchAll(/defaultColList\\.push\\("([^"]+)"\\)/g)]
        .map(m => m[1]);
    const form = document.querySelector('form[name="ParamInfo"]');
    const fd = new FormData(form);
    const param = {};
    for (const [k, v] of fd.entries()) param[k] = v;
    function dump(nodes) {
        return nodes.map(n => ({
            key: n.key, title: n.title, folder: n.folder, data: n.data,
            children: n.children ? dump(n.children) : [],
        }));
    }
    const trees = {};
    for (const ci of js.classInfoList) {
        const id = 'fancytree_' + ci.sn;
        try {
            const t = $.ui.fancytree.getTree('#' + id);
            if (t && t.getRootNode().children
                    && t.getRootNode().children.length) {
                trees[String(ci.sn)] = dump(t.getRootNode().children);
            }
        } catch (e) {}
    }
    return {stat_info: js, row_axis: rows.join(','),
            col_axis: cols.join(','), param, trees};
}"""

_JS_EXPORT = """async ([flJson, rowAxis, colAxis, orderStr, endNum,
                      dataOpt]) => {
    const form = document.querySelector('form[name="ParamInfo"]');
    const fd = new FormData(form);
    const data = {};
    for (const [k, v] of fd.entries()) data[k] = v;
    Object.assign(data, {
        fieldList: JSON.stringify(flJson),
        rowAxis: rowAxis, colAxis: colAxis, orderStr: orderStr,
        startNum: '1', endNum: String(endNum),
        doAnal: 'N', viewKind: '1', viewSubKind: '', view: 'table',
        diviSearchYn: 'N', selectAllFlag: 'N', reqCellCnt: '0',
    });
    const post = async (url, body, ajax) => {
        const b = new URLSearchParams();
        for (const [k, v] of Object.entries(body)) b.append(k, v);
        const headers = {'Content-Type':
            'application/x-www-form-urlencoded;charset=UTF-8'};
        if (ajax) headers['X-Requested-With'] = 'XMLHttpRequest';
        const r = await fetch(url, {method: 'POST', headers,
            body: b.toString()});
        return r.arrayBuffer().then(x => new TextDecoder('utf-8').decode(x));
    };
    const gridForm = Object.assign({}, data, {
        view: 'csv', viewKind: '2', viewSubKind: '2_3', smblYn: 'N',
        downGridFileType: 'csv', downGridCsvType: 'UTF-8', dataOpt: dataOpt,
    });
    let grid = null;
    for (let a = 0; a < 3 && !grid; a++) {
        const txt = await post('/statHtml/downGrid.do', gridForm, true);
        let obj = null;
        if (txt.trim().startsWith('{')) {
            try { obj = JSON.parse(txt); } catch (e) {}
        }
        grid = (obj && obj.file) ? obj.file : null;
        if (!grid) await new Promise(r => setTimeout(r, 3000));
    }
    if (!grid) return {error: 'downGrid_no_token'};
    const down = Object.assign({}, gridForm, {file: grid});
    let csv = null;
    for (let a = 0; a < 3 && (!csv || !csv.length); a++) {
        csv = await post('/statHtml/downNormal.do', down, false);
        if (!csv || !csv.length) {
            await new Promise(r => setTimeout(r, 3000));
        }
    }
    if (!csv || !csv.length) return {error: 'downNormal_empty',
                                     file: grid};
    return {csv, file: grid};
}"""


class KosisSession:
    """Playwright 会话封装：一个 browser 服务多表加载与多次导出。

    用法：
        with KosisSession() as ks:
            csv1, _ = ks.export_csv('101', 'DT_1J22001', months,
                                    class_ids={'1': ['T10']})

    首次加载约 1-4 分钟（viewer 壳 + 原生树初始化）。
    """

    FRAME_TIMEOUT = 480.0   # viewer 壳加载预算（KOSIS 慢时 3-8 分钟）
    RATE_LIMIT = 2.0        # 相邻调用间隔（限速友好）

    def __init__(self, proxy: str | None = None) -> None:
        self._proxy = self._normalize_proxy(proxy)
        self._pw = None
        self._browser = None
        self._page = None
        self._tables: dict[str, dict[str, Any]] = {}
        self._last_call = 0.0

    @staticmethod
    def _normalize_proxy(proxy: str | None) -> str | None:
        if not proxy:
            return None
        return proxy.replace('socks5h://', 'socks5://', 1)

    def __enter__(self) -> 'KosisSession':
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                '韩国官方数据源需要 Playwright：请执行 '
                'pip install "deepmacro[korea]" 安装依赖，并运行 '
                'playwright install chromium 安装浏览器') from e
        self._pw = sync_playwright().start()
        try:
            kw: dict[str, Any] = {'headless': True, 'args': ['--no-sandbox']}
            if self._proxy:
                kw['proxy'] = {'server': self._proxy}
            self._browser = self._pw.chromium.launch(**kw)
            self._page = self._browser.new_page()
            self._goto(KOSIS_BASE + '/index/index.do')
            try:
                self._page.wait_for_function('document.body != null',
                                             timeout=60000)
            except Exception:
                pass
            self._page.wait_for_timeout(2000)  # WAF 放行
            return self
        except Exception:
            self._cleanup()
            raise

    def _cleanup(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def __exit__(self, *exc) -> None:
        self._cleanup()

    def _throttle(self) -> None:
        now = time.time()
        wait = self._last_call + self.RATE_LIMIT - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _goto(self, url: str, attempts: int = 3) -> None:
        last: Exception | None = None
        for a in range(attempts):
            self._throttle()
            try:
                self._page.goto(url, timeout=150000, wait_until='commit')
                return
            except Exception as e:
                last = e
                _log.warning('kosis goto 重试 %s/%s: %s', a + 1, attempts,
                             type(e).__name__)
                self._page.wait_for_timeout(5000)
        raise RuntimeError(f'goto 失败: {url} ({last})')

    def _content_frame(self):
        for f in self._page.frames:
            if 'statHtmlContent' in f.url:
                return f
        return None

    def load_table(self, org_id: str, tbl_id: str) -> dict[str, Any]:
        """加载表 viewer 页并提取全部状态（同表重复调用走缓存）。"""
        key = f'{org_id}/{tbl_id}'
        if not self._page:
            raise RuntimeError('KosisSession 未进入 with 上下文')
        if key in self._tables:
            state = self._tables[key]
            frame = state['frame']
            if not frame.is_detached() and frame in self._page.frames:
                return state
            self._tables.pop(key, None)
        self._goto(f'{STATHTML_URL}?orgId={org_id}&tblId={tbl_id}')
        state: dict[str, Any] = {'org_id': org_id, 'tbl_id': tbl_id}
        deadline = time.time() + self.FRAME_TIMEOUT
        frame = None
        stat_info = None
        while time.time() < deadline:
            frame = self._content_frame()
            if frame is not None:
                try:
                    stat_info = frame.evaluate(_JS_STAT_INFO)
                    if stat_info:
                        break
                except Exception:
                    pass
            self._page.wait_for_timeout(6000)
            frame = None
        if frame is None or stat_info is None:
            raise RuntimeError(f'{tbl_id}: content frame 超时（KOSIS 慢或改版）')
        sns = [str(ci.get('sn')) for ci in
               (stat_info.get('classInfoList') or [])]
        while time.time() < deadline:
            try:
                if frame.evaluate(_JS_TREE_READY, sns):
                    break
            except Exception:
                pass
            self._page.wait_for_timeout(6000)
        dumped = frame.evaluate(_JS_DUMP_FRAME)
        state['frame'] = frame
        state['stat_info'] = dumped['stat_info']
        state['param'] = dumped['param']
        state['row_axis'] = dumped['row_axis']
        state['col_axis'] = dumped['col_axis']
        state['trees'] = dumped['trees']
        if sns and len(dumped['trees']) < len(sns):
            raise RuntimeError(
                f"{tbl_id}: 分类树初始化不完整（期望 {len(sns)} 轴，"
                f"实得 {len(dumped['trees'])}）")
        state['order_str'] = build_order_str(
            dumped['stat_info']['classInfoList'],
            dumped['row_axis'], dumped['col_axis'])
        row_items = 1
        sn_map = {str(ci.get('classId')): ci for ci in
                  dumped['stat_info']['classInfoList']}
        for tok in dumped['row_axis'].split(','):
            if tok in ('TIME', 'ITEM'):
                continue
            ci = sn_map.get(tok)
            row_items *= (ci.get('itmCnt') or 1) if ci else 1
        state['end_num'] = max(540, row_items)
        self._tables[key] = state
        _log.info('kosis %s 加载完成: %s 科目树=%s',
                  tbl_id, dumped['stat_info'].get('tblNm'),
                  {k: len(v) for k, v in dumped['trees'].items()})
        return state

    def export_csv(self, org_id: str, tbl_id: str, months: list[str],
                   item_ids: list[str] | None = None,
                   class_ids: dict[str, list[str]] | None = None,
                   data_opt: str = 'cdko'
                   ) -> tuple[str, dict[str, Any]]:
        """导出指定月份窗口的 CSV → (csv_text, state)。

        months：'YYYYMM' 列表。大窗口注意 1 万格下载上限（调用方按年分片）。
        """
        state = self.load_table(org_id, tbl_id)
        frame = state['frame']
        fl = build_field_list(state['stat_info'], months, item_ids,
                              class_ids, state.get('trees') or {})
        self._throttle()
        result = frame.evaluate(
            _JS_EXPORT,
            [fl, state['row_axis'], state['col_axis'], state['order_str'],
             state['end_num'], data_opt])
        if isinstance(result, dict) and result.get('error'):
            raise RuntimeError(
                f"{tbl_id} 导出失败: {result.get('error')} "
                f"(file={result.get('file')})")
        csv_text = result['csv']
        if len(csv_text) < 50:
            raise RuntimeError(f'{tbl_id} CSV 异常（{len(csv_text)}B）')
        return csv_text, state
