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

_LEVEL_LABEL = {'high': '要紧', 'medium': '一般', 'low': '不急'}
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
_VARIANT_LABEL = {'rule': '规则分 · 按写好的方法算', 'deep': '深度分 · 让 AI 判', 'blended': '混合分 · 0.4×规则 + 0.6×AI'}


def _variant_cards(agg):
    cards = []
    for name in _VARIANT_ORDER:
        v = (agg.get('variants') or {}).get(name)
        if not v:
            continue
        bias = v.get('bias', 0.0) or 0.0
        tone = ('出手偏松（整体打高了）' if bias > 0
                else '出手偏紧（整体打低了）' if bias < 0 else '不偏不倚')
        matched = v.get('matched') or {}
        missing = v.get('missing') or {}
        cells = [
            '判对没 %.0f%%' % (v.get('accuracy', 0.0) * 100),
            '分打偏多少（平均差）%s 分' % _num(v.get('mae', 0.0)),
            '整体%s（%+s 分）' % (tone, _num(bias)),
            '该投的排前面没（nDCG）%s' % _num(v.get('ndcg', 0.0)),
            '排名跟标准答案多像 %s' % _num(v.get('spearman')),
            '该掌握的技能找全没 %s%%' % ('%.0f' % (matched.get('recall', 0.0) * 100)
                                     if matched else '—'),
            '缺技能的误报多不多 %s%%' % ('%.0f' % (missing.get('precision', 0.0) * 100)
                                         if missing else '—'),
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

def _cat_span(cat):
    if not cat:
        return '<span class="muted">—</span>'
    return '<span class="cat cat-%s">%s</span>' % (
        _esc(cat), _esc(_CAT_LABEL.get(cat, cat)))


_RULE_DIM = (('salary_score', '薪资'), ('experience_score', '经验'), ('degree_score', '学历'),
             ('skills_score', '技能'), ('position_score', '职位'), ('ai_bonus', 'AI加成'))


def _detail_row(job):
    """主行下的「展开看详情」行：完整 JD + 打分依据 + 理由 + 技能两侧。"""
    job_info = job.get('job') or {}
    gold = job.get('gold') or {}
    rule = job.get('rule') or {}
    deep = job.get('deep') or {}
    parts = []

    jd = (job_info.get('岗位要求和职责') or '').strip()
    if jd:
        parts.append('<b>岗位要求 / 职责</b>：%s' % _esc(jd))
    meta = ['%s=%s' % (l, _esc(v)) for l, v in (('公司', job_info.get('公司')),
                                                 ('职位', job_info.get('职位')),
                                                 ('薪资', job_info.get('薪资')),
                                                 ('经验', job_info.get('经验')),
                                                 ('学历', job_info.get('学历')),
                                                 ('技能标签', job_info.get('技能标签'))) if v]
    if meta:
        parts.append('<b>岗位信息</b>：%s' % ' · '.join(meta))

    rbits = ['%s=%s' % (l, _num(rule.get(k))) for k, l in _RULE_DIM
             if rule.get(k) is not None]
    if rbits:
        parts.append('<b>规则维度分</b>：%s' % ' · '.join(rbits))
    if rule.get('match_reasons'):
        parts.append('<b>规则判定理由</b>：<ol>%s</ol>'
                     % ''.join('<li>%s</li>' % _esc(r) for r in rule['match_reasons']))
    if rule.get('category_reason'):
        parts.append('<b>投递结论依据</b>：%s' % _esc(rule['category_reason']))
    if deep and deep.get('reason'):
        parts.append('<b>AI 判定理由</b>：%s' % _esc(deep['reason']))
    if deep and deep.get('missing_items'):
        parts.append('<b>AI 判定缺的技能</b>：%s' % _esc('、'.join(deep['missing_items'])))
    if gold.get('reason'):
        parts.append('<b>标准答案依据</b>：%s' % _esc(gold['reason']))
    if job.get('matched_rule') or job.get('gold_matched'):
        parts.append('<b>命中的技能（规则）</b>：%s'
                     % _esc('、'.join(job.get('matched_rule') or [])))
    if job.get('gold_matched'):
        parts.append('<b>命中的技能（gold）</b>：%s' % _esc('、'.join(job.get('gold_matched') or [])))
    if job.get('missing_rule'):
        parts.append('<b>缺失（规则）</b>：%s' % _esc('、'.join(job.get('missing_rule') or [])))
    if job.get('gold_missing'):
        parts.append('<b>缺失（gold）</b>：%s' % _esc('、'.join(job.get('gold_missing') or [])))

    body = '<br>'.join(parts) if parts else '<span class="muted">（无更多明细）</span>'
    return ('<tr class="detail"><td colspan="9"><details>'
            '<summary>展开看详情（JD / 打分依据 / 理由）</summary>'
            '<div class="dbody">%s</div></details></td></tr>' % body)


def _row(job):
    gold = job.get('gold') or {}
    cells = [
        '<td>%d</td>' % job.get('index', 0),
        '<td>%s<br><span class="muted">%s</span></td>'
        % (_esc(job.get('company', '')), _esc(job.get('position', ''))),
        '<td>%s</td>' % _cat_span(gold.get('category')),
        '<td>%s</td>' % _num(gold.get('score')),
    ]
    for name in ('rule', 'deep', 'blended'):
        p = job.get(name) or {}
        cat = p.get('category')
        wrong = bool(cat) and cat != gold.get('category')
        cls = ' wrong' if wrong else ''
        cells.append(
            '<td%s>%s<br><span class="muted">%s</span></td>'
            % (cls, _cat_span(cat), _num(p.get('score100', p.get('score')))))
    matched = job.get('matched_rule') or []
    missing = job.get('missing_rule') or []
    cells.append('<td>%d</td>' % len(matched))
    cells.append('<td class="muted">%s</td>' % _esc('、'.join(missing[:6])))
    return '<tr>%s</tr>' % ''.join(cells)


def _job_rows(jobs_view):
    return '\n'.join(r for j in jobs_view for r in (_row(j), _detail_row(j)))


def _resume_card(resume_view):
    """顶部「简历概览」卡（纯展示，取自 profile）。"""
    if not resume_view:
        return ''
    rv = resume_view or {}
    items = []
    skills = rv.get('skills') or []
    if skills:
        items.append('<span>技能：%s</span>' % _esc('、'.join(skills)))
    exp = rv.get('experience')
    if exp is not None:
        items.append('<span>经验：%g 年</span>' % exp)
    if rv.get('degree'):
        items.append('<span>学历：%s</span>' % _esc(rv.get('degree')))
    smin, smax = rv.get('salary_min'), rv.get('salary_max')
    if smin is not None or smax is not None:
        items.append('<span>期望薪资：%s</span>'
                     % _esc('%s-%sK' % (_num(smin, 0), _num(smax, 0))))
    if not items:
        return ''
    return ('<div class="card"><div class="card-name">简历概览</div>'
            '<div class="kpi">%s</div></div>' % ' '.join(items))


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
tr.detail td{padding:0;border-bottom:none}
tr.detail details{border-top:1px dashed var(--line)}
tr.detail summary{cursor:pointer;padding:6px 10px;color:var(--muted);font-size:12px;user-select:none}
tr.detail summary:hover{color:var(--fg)}
.dbody{padding:4px 14px 14px;font-size:13px;line-height:1.8;color:var(--fg)}
.dbody ol{margin:2px 0 2px 20px;padding:0}
.dbody b{color:var(--muted);font-weight:600}
</style>
</head>
<body><div class="wrap">
<h1>岗位匹配评估 · 这套匹配判得准不准</h1>
<div class="meta">%(meta)s · 共 %(n_jobs)s 个岗位 · 标准答案来源 %(gold_sources)s · 阈值只是参考线，不是定论</div>
<div class="asym">⚠ 先说明一个先天限制：AI 那条路只会列出「缺什么技能」，列不出「会什么技能」，
所以下面「技能找全没」这一项只有规则一种算法有数，AI 和混合这两种只比「缺技能的误报」。
不是我藏着不用，而是这种打法天生就这么个身子。</div>
%(resume)s
%(cards)s
<h2>该往哪儿改（参考建议，不是结论）</h2>
%(suggestions)s
%(llm)s
<h2>逐岗核对（判错的分公司名和职位下面标红，方便你肉眼过一遍）</h2>
<div class="scroll"><table>
<tr><th>#</th><th>公司 / 职位</th><th>标准答案档</th><th>标准答案分</th>
<th>规则判</th><th>AI 判</th><th>混合判</th><th>命中的技能数</th><th>缺了哪些技能</th></tr>
%(rows)s
</table></div>
</div></body></html>
"""


def render_html(recommend_result, jobs_view, meta='', gold_sources='', resume_view=None):
    """推荐结果 → 单文件 HTML 字符串。jobs_view 为逐岗行视图（见模块 docstring）。"""
    agg = recommend_result.get('aggregate', {})
    suggestions = recommend_result.get('suggestions', [])
    llm = recommend_result.get('llm_comment', '')
    llm_html = ('<h2>AI 综合点评</h2><div class="llm"><pre>%s</pre></div>'
                % _esc(llm)) if llm else ''
    return _HTML_TEMPLATE % {
        'meta': _esc(meta),
        'n_jobs': len(jobs_view),
        'gold_sources': _esc(gold_sources),
        'resume': _resume_card(resume_view),
        'cards': _variant_cards(agg),
        'suggestions': ('<table><tr><th>要紧吗</th><th>哪个文件</th><th>啥问题</th>'
                        '<th>怎么改</th><th>凭啥这么说</th></tr>%s</table>'
                        % _suggestion_rows(suggestions)) if suggestions
                        else '<p class="muted">各项指标都还行，没发现特别要紧该改的地方。</p>',
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