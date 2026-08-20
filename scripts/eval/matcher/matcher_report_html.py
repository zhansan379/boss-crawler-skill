#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""匹配评估报告的 HTML + JSON 渲染（镜像 materials/report_html.py）。

`render_html(recommend_result, jobs_view, meta)`：把逐变体 KPI、建议、逐错例行视图渲染成
单文件 HTML（内联 CSS，明暗自适应）。`write_eval_json(path, ...)` 把同一份数据写成结构化 JSON。

jobs_view：每行 = {index, company, position, link, gold:{category,score},
rule:{category,score100,score_native}, deep:{...}, blended:{...}, matched/missing 计数}，
错判（gold.category ≠ 变体.category）在行视图里着色，可肉眼核对。
"""

import os
import json

_LEVEL_LABEL = {'high': '高优先级', 'medium': '中', 'low': '低'}
_CAT_LABEL = {'qualified': '可直接投', 'need_optimization': '优化后投', 'cannot_apply': '不可投'}


def _esc(s):
    return (str(s) or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _num(v, nd=1):
    if v is None:
        return '—'
    if isinstance(v, int):
        return str(v)
    return ('%.*f' % (nd, v))


# ==================== 逐变体 KPI 卡片 ====================

_VARIANT_ORDER = ('rule', 'deep', 'blended')
_VARIANT_LABEL = {'rule': '规则分', 'deep': '深度分', 'blended': '混合 0.4·规则+0.6·深度'}


def _variant_cards(agg):
    cards = []
    for name in _VARIANT_ORDER:
        v = (agg.get('variants') or {}).get(name)
        if not v:
            continue
        cells = [
            'accuracy %.0f%%' % (v.get('accuracy', 0.0) * 100),
            'MAE %s' % _num(v.get('mae', 0.0)),
            'bias %+s' % _num(v.get('bias', 0.0)),
            'nDCG %s' % _num(v.get('ndcg', 0.0)),
            'Spearman %s' % _num(v.get('spearman')),
            'matched R %s%%' % ('%.0f' % (v.get('matched', {}).get('recall', 0.0) * 100)
                                if v.get('matched') else '—'),
            'missing P %s%%' % ('%.0f' % (v.get('missing', {}).get('precision', 0.0) * 100)
                                if v.get('missing') else '—'),
        ]
        cards.append('  <div class="card"><div class="card-name">%s%s</div>'
                     '<div class="kpi">%s</div>'
                     % (_esc(_VARIANT_LABEL.get(name, name)),
                        '<div class="muted">%s</div>' % _esc(v.get('note', '')) if v.get('note') else '',
                        ' '.join('<span>%s</span>' % _esc(c) for c in cells)))
    return '\n'.join(cards)


def _suggestion_rows(suggestions):
    rows = []
    for s in suggestions:
        rows.append(
            '<tr><td><span class="lvl lvl-%s">%s</span></td>'
            '<td><code>%s</code></td><td>%s</td><td>%s</td><td class="muted">%s</td></tr>'
            % (_esc(s.get('level', 'low')), _esc(_LEVEL_LABEL.get(s.get('level', 'low'))),
               _esc(s.get('file', '')), _esc(s.get('issue', '')),
               _esc(s.get('action', '')), _esc(s.get('evidence', ''))))
    return '\n'.join(rows)


# ==================== 逐错例行视图 ====================

def _cat_td(cat):
    return '<td><span class="cat cat-%s">%s</span></td>' % (
        _esc(cat), _esc(_CAT_LABEL.get(cat, cat)))


def _row(job):
    gold = job.get('gold') or {}
    cells = [
        '<td>%d</td>' % job.get('index', 0),
        '<td>%s<br><span class="muted">%s</span></td>'
        % (_esc(job.get('company', '')), _esc(job.get('position', ''))),
        _cat_td(gold.get('category')),
        '<td>%s</td>' % _num(gold.get('score')),
    ]
    for name in ('rule', 'deep', 'blended'):
        p = job.get(name) or {}
        cat = p.get('category')
        wrong = bool(cat) and cat != gold.get('category')
        cls = ' wrong' if wrong else ''
        cells.append(
            '<td%s>%s<br><span class="muted">%s</span></td>'
            % (cls, _cat_td(cat), _num(p.get('score100', p.get('score')))))
    matched = job.get('matched_rule') or []
    missing = job.get('missing_rule') or []
    cells.append('<td>%d</td>' % len(matched))
    cells.append('<td class="muted">%s</td>' % _esc('、'.join(missing[:6])))
    return '<tr>%s</tr>' % ''.join(cells)


def _job_rows(jobs_view):
    return '\n'.join(_row(j) for j in jobs_view)


# ==================== HTML 壳 ====================

_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>岗位匹配评估 · %(n_jobs)s 岗</title>
<style>
:root{--bg:#f5f7fa;--fg:#1c2530;--muted:#667085;--card:#fff;--line:#e4e7ec;--warn:#b54708;--bad:#e5484d;--good:#25a06c;--hl:#fafbfc;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e1117;--fg:#dbe2ea;--muted:#8b93a1;--card:#161b26;--line:#262b36;--hl:#141926;}}
:root[data-theme=dark]{--bg:#0e1117;--fg:#dbe2ea;--muted:#8b93a1;--card:#161b26;--line:#262b36;--hl:#141926;}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:1200px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}
.meta{color:var(--muted);font-size:12px;margin-bottom:20px}
.asym{background:var(--hl);border-left:3px solid var(--warn);padding:10px 14px;border-radius:6px;
font-size:13px;color:var(--muted);margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card-name{font-weight:600;margin-bottom:8px}.kpi span{display:inline-block;margin:2px 4px;
background:var(--hl);border:1px solid var(--line);border-radius:20px;padding:2px 10px;font-size:12px}
.muted{color:var(--muted);font-size:12px;font-weight:400}
h2{font-size:16px;margin:24px 0 10px}
table{width:100%%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:var(--hl);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.wrong{background:rgba(229,72,77,.08)}
.cat{display:inline-block;padding:1px 8px;border-radius:12px;font-size:12px}
.cat-qualified{background:rgba(37,160,108,.14);color:var(--good)}
.cat-need_optimization{background:rgba(181,71,8,.14);color:var(--warn)}
.cat-cannot_apply{background:rgba(229,72,77,.14);color:var(--bad)}
.lvl{display:inline-block;padding:1px 8px;border-radius:12px;font-size:12px}
.lvl-high{background:rgba(229,72,77,.14);color:var(--bad)}
.lvl-medium{background:rgba(181,71,8,.14);color:var(--warn)}
.lvl-low{background:rgba(102,113,133,.14);color:var(--muted)}
code{background:var(--hl);border:1px solid var(--line);border-radius:4px;padding:1px 5px;
font-size:12px}pre{white-space:pre-wrap;font-size:13px}
.scroll{overflow-x:auto;border-radius:10px}
.llm{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
</style>
</head>
<body><div class="wrap">
<h1>岗位匹配评估 · 四族指标</h1>
<div class="meta">%(meta)s · %(n_jobs)s 个岗位 · gold 来源 %(gold_sources)s · 阈值均为启发读数</div>
<div class="asym">⚠ 深度/LLM 侧不产 matched_skills，只产 missing：本报告的 matched 指标仅对
规则路径计算；deep / blended 只比 missing（gold 的 matched/missing 均来自独立 oracle）。</div>
%(cards)s
<h2>阈值→建议（启发，非结论）</h2>
%(suggestions)s
%(llm)s
<h2>逐岗核对（错判高亮）</h2>
<div class="scroll"><table>
<tr><th>#</th><th>公司 / 职位</th><th>gold 档</th><th>gold 分</th>
<th>规则</th><th>深度</th><th>混合</th><th>命中</th><th>规则缺失</th></tr>
%(rows)s
</table></div>
</div></body></html>
"""


def render_html(recommend_result, jobs_view, meta='', gold_sources=''):
    """推荐结果 → 单文件 HTML 字符串。jobs_view 为逐岗行视图（见模块 docstring）。"""
    agg = recommend_result.get('aggregate', {})
    suggestions = recommend_result.get('suggestions', [])
    llm = recommend_result.get('llm_comment', '')
    llm_html = ('<h2>LLM 综合点评</h2><div class="llm"><pre>%s</pre></div>'
                % _esc(llm)) if llm else ''
    return _HTML_TEMPLATE % {
        'meta': _esc(meta),
        'n_jobs': len(jobs_view),
        'gold_sources': _esc(gold_sources),
        'cards': _variant_cards(agg),
        'suggestions': ('<table><tr><th>优先级</th><th>文件</th><th>问题</th>'
                        '<th>建议动作</th><th>证据</th></tr>%s</table>'
                        % _suggestion_rows(suggestions)) if suggestions
                        else '<p class="muted">各项指标均在阈值内，暂无高优先建议。</p>',
        'llm': llm_html,
        'rows': _job_rows(jobs_view),
    }


def write_eval_json(path, recommend_result, jobs_view, meta=None):
    """聚合 + 建议 + 逐岗明细落盘成一条 JSON。"""
    payload = {
        'meta': meta or '',
        'aggregate': recommend_result.get('aggregate', {}),
        'variants': recommend_result.get('variants', {}),
        'suggestions': recommend_result.get('suggestions', []),
        'llm_comment': recommend_result.get('llm_comment') or '',
        'jobs': jobs_view,
    }
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path