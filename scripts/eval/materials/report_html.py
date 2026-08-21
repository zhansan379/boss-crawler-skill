#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把评估结果渲染成自包含 HTML 报告 + eval.json。

HTML 是 dataviz 风格：浅/深双主题共用同一组 CSS 变量、统一 palette，无外部依赖、无 CDN。
模板用 printf 风格占位（`%(name)s`），因为 CSS 有大量 `{}`，不能走 str.format。

报告只读不写：文件在 evaluate_materials.py 里落盘。三个类别统一配色：
保留=绿 · 岗位驱动=蓝 · 无据(幻觉)=红。所有读数都是启发，报告用「启发」标注，不冒充结论。

术语三分类支持两种来源：rule（白名单规则，纯算数）与 llm（语义分类，带逐词 reason）。
LLM 来源时，「优化前 vs 优化后」对照面板会把每词按来路着色，悬停看理由；rule 来源仅纯文本对照。
"""

import os
import json

_LEVEL_LABEL = {'high': '高优先级', 'medium': '中', 'low': '低'}


def _num(v, suffix='%'):
    try:
        return '%.1f%s' % (float(v) * 100, suffix)
    except (TypeError, ValueError):
        return '0%s' % suffix


def _esc(text):
    """HTML 转义，防无据词带 <script> 等把布局打穿。"""
    t = str(text or '')
    return (t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;'))


# ==================== 术语着色基元 ====================

_BUCKET_CSS = {'retained': '#25a06c', 'jd_driven': '#3b82f6', 'unfounded': '#e5484d'}
_BUCKET_LABEL = {'retained': '保留', 'jd_driven': '岗位驱动', 'unfounded': '无据(幻觉)'}


def _replace_case(src, token, meta=None):
    """把 src 里所有 token（大小写不敏感、整词边界）包成带色 span。meta 为空则仅转义。"""
    if not token:
        return src
    n, out, o = len(token), '', 0
    low_src, low_tok = src.lower(), token.lower()
    while True:
        idx = low_src.find(low_tok, o)
        if idx < 0:
            break
        pts = not (idx > 0 and (src[idx - 1].isalnum() or src[idx - 1] in '_-'))
        pte = not (idx + n < len(src) and (src[idx + n].isalnum() or src[idx + n] in '_-'))
        if meta and pts and pte:
            css, label = meta['css'], meta['label']
            reason = meta.get('reason', '')
            out += _esc(src[o:idx])
            out += ('<span class="tterm" style="background:%s22;color:%s;'
                    'border-radius:3px;padding:0 2px" title="[%s] %s">'
                    % (css, css, label, _esc(reason)))
            out += _esc(src[idx:idx + n])
            out += '</span>'
        else:
            out += _esc(src[o:idx + n])
        o = idx + n
    return out + _esc(src[o:])


def _highlight_terms(src, terms):
    """单次扫描把 src 里所有命中术语包成带色 span，一次成型，绝不二次转义。

    若逐词调用 _replace_case，后一个词会把前一个词已插好的 `<span>` 当纯文本再
    _esc 一遍，产生 &amp;amp;lt;span… 的双倍转义。这里是收集全部 (起,止,css,label,
    reason) 后按位置排序，一次性输出：非命中区间 _esc，命中区间直接插 span。
    同义词同词形只取第一桶（terms 已按优先级排序）。
    """
    hits = []
    low = src.lower()
    min_len = 2                            # 单个字符切词无意义，跳过
    for t in terms or []:
        tok = (t.get('term') or '').strip()
        if not tok or len(tok) < min_len:
            continue
        css = _BUCKET_CSS.get(t.get('bucket'), '#999')
        label = _BUCKET_LABEL.get(t.get('bucket'), t.get('bucket', '?'))
        # 悬停说明：LLM 给 reason（带理由），规则路没有 reason 就退到 context（那词在哪句）
        reason = t.get('reason') or t.get('context') or ''
        low_tok, n = tok.lower(), len(tok)
        o = 0
        while True:
            idx = low.find(low_tok, o)
            if idx < 0:
                break
            o = idx + n
            pts = not (idx > 0 and (src[idx - 1].isalnum() or src[idx - 1] in '_-'))
            pte = not (idx + n < len(src) and (src[idx + n].isalnum() or src[idx + n] in '_-'))
            if pts and pte:
                hits.append((idx, idx + n, css, label, reason))
    if not hits:
        return _esc(src)
    hits.sort(key=lambda h: (h[0], -h[1]))
    picks = []
    for h in hits:
        if picks and h[0] < picks[-1][1]:  # 与已被更长/更早命中覆盖或重叠，跳过
            continue
        picks.append(h)
    out, o = [], 0
    for s, e, css, label, reason in picks:
        out.append(_esc(src[o:s]))
        # o 到 s 之间可能残留未命中的小段，直接进转义
        out.append('<span class="tterm" style="background:%s22;color:%s;'
                   'border-radius:3px;padding:0 2px" title="[%s] %s">'
                   % (css, css, label, _esc(reason)))
        out.append(_esc(src[s:e]))
        out.append('</span>')
        o = e
    out.append(_esc(src[o:]))
    return ''.join(out)


def render_html(recommend_result, jobs_view, meta=None):
    """渲染整份 HTML。recommend_result 来自 recommend.recommend()，jobs_view 是逐岗明细。

    jobs_view 每项：{index, company, position, greeting_preview, greeting_full,
    status('ok'|'missing'), metrics(evaluate_job 结果),
    base_text / optimized_resume / terms_mode（对照面板用）}。
    """
    agg = recommend_result.get('aggregate', {})
    suggestions = recommend_result.get('suggestions', [])
    llm_comment = recommend_result.get('llm_comment') or ''

    t = agg.get('terms', {})
    cd = agg.get('char_diff', {})
    gr = agg.get('greeting', {})
    ch = agg.get('chapters', {})

    # ── KPI 卡 ──
    kpi_spec = [
        ('平均无据术语(幻觉)', t.get('avg_hallucination_pct', 0), 'bad',
         '优化后既不在简历也不在 JD 的术语占比'),
        ('岗位驱动新增', t.get('avg_jd_driven_pct', 0), 'ok',
         '新增但 JD 里有依据的术语占比（合理靠岗）'),
        ('原文保留(字符)', cd.get('avg_coverage_pct', 1), 'warn',
         '优化后原文字符仍有 %% 保留'),
        ('章节缺失', ch.get('n_jobs_missing', 0) and '%d/%d 岗' % (ch.get('n_jobs_missing'), agg.get('n_jobs', 0)) or '0',
         'bad' if ch.get('n_jobs_missing') else 'ok', '优化简历丢掉原章节的岗位数'),
        ('前15字客套', '%d 条' % gr.get('n_wasted_preview', 0),
         'bad' if gr.get('n_wasted_preview') else 'ok', 'HR 只见前 15 字的招呼语'),
        ('编造到岗承诺', '%d 条' % gr.get('n_fabricated_commitment', 0),
         'bad' if gr.get('n_fabricated_commitment') else 'ok',
         '简历没写到岗/出勤却出现在招呼语'),
    ]
    # 数字型 KPI 单独再转
    kpi_spec[0] = (kpi_spec[0][0], _num(t.get('avg_hallucination_pct', 0)), kpi_spec[0][2], kpi_spec[0][3])
    kpi_spec[1] = (kpi_spec[1][0], _num(t.get('avg_jd_driven_pct', 0)), kpi_spec[1][2], kpi_spec[1][3])
    kpi_spec[2] = (kpi_spec[2][0], _num(cd.get('avg_coverage_pct', 1)), kpi_spec[2][2], kpi_spec[2][3])
    kpis = '\n'.join(
        '<div class="tile %s"><div class="k">%s</div><div class="v">%s</div>'
        '<div class="d">%s</div></div>' % (cls, key, val, desc)
        for key, val, cls, desc in kpi_spec)

    # ── 术语堆叠条 ──
    def _chips(lst, color, label):
        """某一 bucket 的具体术语标签（不只看比例）。"""
        if not lst:
            return ''
        words = ''.join(
            '<span class="chip" style="color:%s;border-color:%s">%s</span>'
            % (color, color, _esc(w.get('term')))
            for w in lst)
        return '<div class="chips"><b class="chk" style="color:%s">%s</b>%s</div>' \
               % (color, label, words)

    def term_bar(j):
        m = j.get('metrics', {}).get('terms', {}) if j.get('metrics') else {}
        n = m.get('n_opt', 0)
        tag = '🟥' if m.get('hallucination_pct', 0) > 0 else '✅'
        if not n:
            return '<div class="row"><span class="n">%s #%s %s</span><span class="empty">无可用术语</span></div>' \
                   % (tag, j.get('index'), j.get('company'))
        r, d, u = m.get('n_retained', 0), m.get('n_jd_driven', 0), m.get('n_unfounded', 0)
        seg = ('<div style="width:%.1f%%;background:#25a06c" title="保留 %d"></div>'
               '<div style="width:%.1f%%;background:#3b82f6" title="岗位驱动 %d"></div>'
               '<div style="width:%.1f%%;background:#e5484d" title="无据 %d"></div>'
               % (r / n * 100, r, d / n * 100, d, u / n * 100, u))
        head = ('<div class="row"><span class="n">%s #%s %s</span><span class="bar">%s</span>'
                '<span class="ratio">%d/%d/%d</span></div>'
                % (tag, j.get('index'), j.get('company'), seg, r, d, u))
        detail = (_chips(m.get('retained'), '#25a06c', '保留')
                  + _chips(m.get('jd_driven'), '#3b82f6', '岗位驱动')
                  + _chips(m.get('unfounded'), '#e5484d', '无据'))
        return head + detail if detail else head

    term_bars = '\n'.join(term_bar(j) for j in jobs_view) or '<p>无可用岗位</p>'

    # ── 优化前 vs 优化后（LLM 术语高亮）──
    def compare_card(j):
        if j.get('status') == 'missing':
            return ''
        base = (j.get('base_text') or '').strip()
        opt = (j.get('optimized_resume') or '').strip()
        m = ((j.get('metrics') or {}).get('terms', {})
             if (j.get('metrics') or {}).get('terms') else {})
        mode = m.get('mode') if isinstance(m, dict) else None
        terms = m.get('terms') if isinstance(m, dict) else None
        analysis = (m.get('analysis') or '') if isinstance(m, dict) else ''

        # rule/llm 都有统一 terms list（含 bucket）→ 都逐词着色；缺明细才纯文本
        if isinstance(terms, list) and terms:
            hl_opt = _highlight_terms(opt, terms)
        else:
            hl_opt = _esc(opt or '（无产物）')
        opt_header = '优化后 · LLM 语义分类' if mode == 'llm' else '优化后 · 规则分类'
        opt_col = ('<div class="cmp-col opt"><h4>%s</h4><pre>%s</pre></div>'
                   % (opt_header, hl_opt))
        base_col = ('<div class="cmp-col base"><h4>优化前（原简历）</h4>'
                    '<pre>%s</pre></div>' % _esc(base or '（无基准文本）'))
        ana = ('<p class="cmp-ana">LLM 点评：%s</p>' % _esc(analysis)
               if analysis else '')
        tag = '<span class="bd %s">%s</span>' % (
            'ok' if mode == 'llm' else ('warn' if mode else ''),
            'LLM 语义分类' if mode == 'llm' else '规则分类')
        return ('<details class="cmp" %s><summary>#%s %s / %s %s</summary>%s'
                '<div class="cmp-grid">%s%s</div></details>'
                % ('open' if mode == 'llm' else '', j.get('index'),
                   _esc(j.get('company') or ''), _esc(j.get('position') or ''),
                   tag, ana, base_col, opt_col))

    compare_cards = '\n'.join(c for j in jobs_view
                              if (c := compare_card(j))) or '<p>（无）</p>'

    # ── 字符级 diff 明细表 ──
    char_rows = []
    for j in jobs_view:
        c = (j.get('metrics') or {}).get('char_diff', {}) if j.get('metrics') else {}
        char_rows.append(
            '<tr><td>#%s %s</td><td>%s</td><td>%s</td><td>%s</td><td>%d → %d</td></tr>'
            % (j.get('index'), j.get('company'), _num(c.get('deleted_pct', 0)),
               _num(c.get('added_pct', 0)), _num(c.get('coverage_pct', 1)),
               c.get('base_len', 0), c.get('opt_len', 0)))
    char_table = ('<table><thead><tr><th>岗位</th><th>删除%%</th><th>新增%%</th>'
                  '<th>原文覆盖%%</th><th>字符 基→优化</th></tr></thead>'
                  '<tbody>%s</tbody></table>' % '\n'.join(char_rows)
                  if char_rows else '<p class="good">无</p>')

    # ── 无据术语表 ──
    rows = []
    for j in jobs_view:
        for f in ((j.get('metrics') or {}).get('terms', {}).get('unfounded') or []):
            rows.append('<tr><td>#%s %s</td><td><code>%s</code></td><td>%s</td></tr>'
                        % (j.get('index'), j.get('company'), _esc(f.get('term')),
                           _esc(f.get('context'))))
    unfounded_table = ('<table><thead><tr><th>岗位</th><th>术语</th><th>上下文</th></tr></thead>'
                       '<tbody>%s</tbody></table>' % '\n'.join(rows)
                       if rows else '<p class="good">无据术语 0 条 —— 干净。</p>')

    # ── 招呼语卡片 ──
    def greet_card(j):
        g = (j.get('metrics') or {}).get('greeting', {}) if j.get('metrics') else {}
        badges = []
        if g.get('has_wasted_preview'):
            badges.append('<span class="bd bad">前15字客套</span>')
        if g.get('fabricated_commitment'):
            badges.append('<span class="bd bad">编造到岗承诺</span>')
        if g.get('has_quantified'):
            badges.append('<span class="bd ok">有量化</span>')
        if g.get('n_unfounded'):
            badges.append('<span class="bd warn">无据词 %d</span>' % g.get('n_unfounded'))
        if not badges:
            badges.append('<span class="bd ok">干净</span>')
        miss = ' class="miss"' if j.get('status') == 'missing' else ''
        return ('<div%s class="gc"><div class="gh">#%s %s / %s</div>'
                '<div class="gp">%s<span class="caret">▌</span></div>'
                '<div class="gb">%s</div></div>'
                % (miss, j.get('index'), j.get('company'), j.get('position'),
                   _esc(j.get('greeting_preview') or '（无产物）'), '\n'.join(badges)))

    greet_cards = '\n'.join(greet_card(j) for j in jobs_view) or '<p>（无）</p>'

    # ── 章节缺失 ──
    ch_rows = []
    for j in jobs_view:
        missing = (j.get('metrics') or {}).get('chapters', {}).get('missing_chapters') or []
        if missing:
            ch_rows.append('<tr><td>#%s %s</td><td>%s</td></tr>'
                           % (j.get('index'), j.get('company'), '、'.join(missing)))
    chapters_table = ('<table><thead><tr><th>岗位</th><th>丢失章节</th></tr></thead>'
                      '<tbody>%s</tbody></table>' % '\n'.join(ch_rows)
                      if ch_rows else '<p class="good">没有章节被删除。</p>')

    # ── 客观性 ──
    subj = []
    for j in jobs_view:
        s = (j.get('metrics') or {}).get('subjective', {}) if j.get('metrics') else {}
        for u in s.get('upgrades', [])[:3]:
            subj.append('<li>#%s %s：<code>%s</code> 升级 → %s</li>'
                        % (j.get('index'), j.get('company'), _esc(u.get('segment')),
                           _esc(u.get('strong'))))
        for a in s.get('absolute_added', [])[:3]:
            subj.append('<li>#%s %s：新增绝对表述 <code>%s</code>（%s）</li>'
                        % (j.get('index'), j.get('company'), _esc(a.get('word')),
                           _esc(a.get('segment'))))
    subjective_html = ('<ul class="subj">%s</ul>' % '\n'.join(subj)
                       if subj else '<p class="good">未发现能力副词升级/绝对化用语。</p>')

    # ── 建议按文件分组 ──
    by_file = {}
    for s in suggestions:
        by_file.setdefault(s.get('file'), []).append(s)
    if llm_comment:
        llm_html = ('<section class="panel"><h3>LLM 综合点评（--llm-recommend）</h3>'
                    '<p class="llm">%s</p></section>' % _esc(llm_comment))
    else:
        llm_html = ''
    sugg = []
    for fname, items in by_file.items():
        inner = '\n'.join(
            '<div class="su %s"><div class="su-h"><b>[%s] %s</b>'
            '<span class="ev">%s</span></div><div class="su-a">%s</div></div>'
            % (s.get('level'), _LEVEL_LABEL.get(s.get('level'), '?'),
               _esc(s.get('issue')), _esc(s.get('evidence', '')),
               _esc(s.get('action')))
            for s in items)
        sugg.append('<section class="panel"><h3><code>%s</code></h3>%s</section>'
                    % (_esc(fname), inner))
    if not sugg and not llm_html:
        sugg = ['<p class="good">没有任何阈值命中 —— 这套 prompt 目前很健康。</p>']

    return _HTML_TEMPLATE % dict(
        kpis=kpis, term_bars=term_bars, compare_cards=compare_cards,
        char_table=char_table,
        unfounded_table=unfounded_table,
        greet_cards=greet_cards, chapters_table=chapters_table,
        subjective_html=subjective_html,
        sugg_html='\n'.join(sugg), llm_html=llm_html,
        meta_html=('<p class="meta">%s</p>' % _esc(meta)) if meta else
                  '<p class="meta">evaluate_materials 质量评估报告 · 指标均为启发读数</p>',
        n_jobs=len(jobs_view),
        avg_hall=_num(t.get('avg_hallucination_pct', 0)),
        avg_cov=_num(cd.get('avg_coverage_pct', 1)),
    )


def write_eval_json(path, recommend_result, jobs_view, meta=None):
    """聚合 + 建议 + 逐岗明细落盘成一条 JSON。"""
    payload = {
        'meta': meta or '',
        'aggregate': recommend_result.get('aggregate', {}),
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


# ==================== HTML 壳 ====================
_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>材料生成质量评估 · %(n_jobs)s 岗</title>
<style>
:root{--bg:#f5f7fa;--fg:#1c2530;--muted:#667085;--card:#fff;--line:#e4e7ec;--warn:#b54708;--bad:#e5484d;--good:#25a06c;--hl:#fafbfc;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e1117;--fg:#dbe2ea;--muted:#8b93a1;--card:#161b26;--line:#262b36;--hl:#141926;}}
:root[data-theme=dark]{--bg:#0e1117;--fg:#dbe2ea;--muted:#8b93a1;--card:#161b26;--line:#262b36;--hl:#141926;}
*{box-sizing:border-box}body{background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:24px;max-width:1080px;margin-inline:auto}
.topbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:space-between}
h1{font-size:20px;margin:0}h3{font-size:15px;margin:18px 0 8px}.meta{color:var(--muted);font-size:13px;margin:6px 0 0}
button{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:4px 12px;cursor:pointer;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.tile .k{font-size:12px;color:var(--muted)}.tile .v{font-size:21px;font-weight:700;margin:2px 0}.tile .d{font-size:11px;color:var(--muted)}
.tile.bad .v{color:var(--bad)}.tile.warn .v{color:var(--warn)}.tile.ok .v{color:var(--good)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
.row{display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--hl)}.row .n{min-width:190px;font-size:13px}
.bar{flex:1;display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--hl);min-width:120px}.bar>div{height:100%%}
.ratio{min-width:86px;text-align:right;color:var(--muted);font-size:12px}.empty{color:var(--muted);font-size:12px}
.chips{display:flex;align-items:center;flex-wrap:wrap;gap:5px;padding:2px 0 6px;font-size:12px}
.chips .chk{min-width:52px;font-weight:600}.chip{display:inline-block;border:1px solid;border-radius:10px;padding:0 8px;background:var(--card)}
table{width:100%%;border-collapse:collapse;font-size:13px;margin-top:8px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--hl)}th{color:var(--muted);font-weight:600}
code{background:var(--hl);border:1px solid var(--line);border-radius:5px;padding:0 5px;font-size:12px}.good{color:var(--good)}
.gc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0;max-width:620px}
.gc.miss{opacity:.55}.gh{font-size:12px;color:var(--muted);margin-bottom:4px}.gp{font-size:14px;letter-spacing:.2px}.caret{color:var(--line);opacity:.5}
.gb{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}.bd{border-radius:5px;padding:1px 7px;font-size:11px}
.bd.bad{background:#e5484d22;color:var(--bad)}.bd.ok{background:#25a06c22;color:var(--good)}.bd.warn{background:#b5470822;color:var(--warn)}
ul.subj{list-style:none;padding:0;margin:8px 0}ul.subj li{padding:4px 0;font-size:13px}
.su{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0}.su-h{font-size:13px}.su .ev{color:var(--muted);font-size:12px;float:right}.su-a{color:var(--muted);font-size:13px;margin-top:4px}
.su.high{border-left:3px solid var(--bad)}.su.medium{border-left:3px solid var(--warn)}.su.low{border-left:3px solid var(--good)}
.llm{background:var(--hl);border:1px solid var(--line);border-radius:8px;padding:12px;font-size:13px}
details.cmp{background:var(--hl);border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin:8px 0}
details.cmp summary{cursor:pointer;font-size:13px;font-weight:600}
details.cmp .bd{vertical-align:middle;margin-left:6px}
.cmp-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
.cmp-col{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--card)}
.cmp-col h4{font-size:12px;color:var(--muted);margin:0 0 6px}
.cmp-col pre{white-space:pre-wrap;word-break:break-word;font:13px/1.5 "Microsoft YaHei",sans-serif;margin:0;max-height:340px;overflow:auto}
.cmp-ana{font-size:12px;color:var(--muted);margin:6px 0 0;border-top:1px dashed var(--line);padding-top:6px}
@media (max-width:760px){.cmp-grid{grid-template-columns:1fr}}
</style></head><body>
<div class="topbar"><h1>材料生成质量评估</h1>
<button onclick="var r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark'">深浅主题</button></div>
<div class="meta">评估 %(n_jobs)s 个岗位的招呼语 + 优化简历 · 平均无据术语 %(avg_hall)s · 平均原文保留 %(avg_cov)s</div>
%(meta_html)s
%(llm_html)s
<div class="kpis">%(kpis)s</div>
<section class="panel"><h3>术语三分类 · 每岗 100%% 堆叠
      <span style="font-weight:400;color:var(--muted)">
        <b style="color:#25a06c">■</b>保留
        <b style="color:#3b82f6">■</b>岗位驱动
        <b style="color:#e5484d">■</b>无据(幻觉)
      </span></h3>%(term_bars)s</section>
<section class="panel"><h3>优化前 vs 优化后 · 逐词对照（LLM 分类含理由，悬停查看）</h3>%(compare_cards)s</section>
<section class="panel"><h3>字符级 删 / 增 / 覆盖</h3>%(char_table)s</section>
<section class="panel"><h3>招呼语 preview（HR 只看前 15 字）</h3>%(greet_cards)s</section>
<section class="panel"><h3>无据术语逐条</h3>%(unfounded_table)s</section>
<section class="panel"><h3>章节保真（优化简历是否删掉原章节）</h3>%(chapters_table)s</section>
<section class="panel"><h3>客观性提示（能力升级 / 绝对化，启发）</h3>%(subjective_html)s</section>
<h3>提示词优化建议</h3>%(sugg_html)s
<p class="meta">数据见同目录 eval.json；生成与校验复用 skill 原逻辑（gen_materials + verify_no_fabrication）。</p>
</body></html>"""