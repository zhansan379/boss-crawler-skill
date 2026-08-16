#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""link → 匹配信息的合并索引，以及给读取侧的 ranked 视图。

为什么单独一个模块：这段合并逻辑原本长在 `gen_materials.py` 里（写材料侧），
读取侧（`read_thin.py`）拿不到，于是主代理每次想看「分数 + 判定 + 公司 + 职位」
都得自己拼 —— 实测拼了 4 条一行流，还跑出过 `matched: 0 / 18`。

问题不在于原理没写清（references 里写着按 `rank` 对齐），而在于知道原理之后
仍然没有现成工具。抽到这里之后 `gen_materials` 和 `read_thin` 共用一份，
两边不会再各自漂移。

只依赖标准库，这样两边 import 都不会拖进第三方依赖。
"""

import json
import os


def _norm_list(value, limit=None):
    if isinstance(value, list):
        out = [str(v).strip() for v in value if v is not None and str(v).strip()]
    elif value and isinstance(value, str):
        out = [value.strip()]
    else:
        out = []
    return out[:limit] if limit else out


def build_match_index(run_dir):
    """link → 匹配信息（match_score / category / match_reasons / missing_items / …）。

    匹配信息散在三个文件里，取决于走的是快速模式还是深度模式；qualified_jobs.json
    本身只有原始爬取字段，没有分数。这里按「精细度」从低到高叠加，后来者覆盖前者：

        scored_jobs.json（规则分）→ job_classification.json（规则视图）
        → deep_results.json（模型分，最权威）

    一个都没有也能跑：招呼语可以只靠 JD + 简历写，简历优化会拿不到缺失项提示 ——
    质量会掉，但不该因此拒绝执行（用户可能只跑了 crawl + 手写 qualified_jobs.json）。
    """
    index = {}

    def put(link, **fields):
        if not link:
            return
        slot = index.setdefault(link, {})
        for key, value in fields.items():
            if value:                              # 空值不覆盖已有的更好的值
                slot[key] = value

    # ── 1. scored_jobs.json：规则侧原始 tier ──
    scored = os.path.join(run_dir, 'scored_jobs.json')
    if os.path.exists(scored):
        try:
            with open(scored, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for tier in ('tier4', 'tier3', 'tier2', 'tier1'):
                for job in (data.get(tier) or []):
                    put(job.get('link'),
                        match_score=job.get('match_score'),
                        category=job.get('application_category'),
                        match_reasons=_norm_list(job.get('match_reasons')),
                        missing_items=_norm_list(job.get('missing_skills')),
                        optimization_points=_norm_list(job.get('optimization_points')))
        except (ValueError, OSError):
            pass

    # ── 2. job_classification.json：build_job_view 之后的视图 ──
    classification = os.path.join(run_dir, 'job_classification.json')
    if os.path.exists(classification):
        try:
            with open(classification, 'r', encoding='utf-8') as f:
                data = json.load(f)
            buckets = data.get('classification') or {}
            for name in ('cannot_apply', 'need_optimization', 'qualified'):
                for view in (buckets.get(name) or []):
                    reasons = _norm_list(view.get('classification_reason'))
                    put(view.get('link'),
                        match_score=view.get('match_score'),
                        # 桶名本身就是判定，比字段更可靠：视图里 application_category
                        # 缺失时（手写/旧产物）用桶名兜底。
                        category=view.get('application_category') or name,
                        match_reasons=reasons,
                        missing_items=_norm_list(view.get('missing_items')),
                        optimization_points=_norm_list(view.get('optimization_points')),
                        highlight=view.get('highlight'))
        except (ValueError, OSError):
            pass

    # ── 3. deep_results.json：模型逐岗分析，rank 经 deep_candidates 换成 link ──
    # 这两个文件之间**唯一**的对齐键是 rank；deep_results 自己不含 link，
    # 也不含公司职位。rank 错位不会报任何错，只会把一个岗位的分析安到另一个岗位上。
    cand_path = os.path.join(run_dir, 'deep_candidates.json')
    deep_path = os.path.join(run_dir, 'deep_results.json')
    if os.path.exists(cand_path) and os.path.exists(deep_path):
        try:
            with open(cand_path, 'r', encoding='utf-8') as f:
                candidates = json.load(f).get('candidates') or []
            with open(deep_path, 'r', encoding='utf-8') as f:
                results = json.load(f).get('results') or []
            by_rank = {r.get('rank'): r for r in results if isinstance(r, dict)}
            for candidate in candidates:
                deep = by_rank.get(candidate.get('rank'))
                if not deep:
                    continue
                reasons = _norm_list(deep.get('reason') or deep.get('classification_reason'))
                put((candidate.get('job') or {}).get('link'),
                    match_score=deep.get('score'),
                    # 深度侧这个键叫 category，不叫 application_category（deep_analyze
                    # 写 deep_results 时用的是前者，写 job_classification 用的是后者）。
                    category=deep.get('category') or deep.get('application_category'),
                    match_reasons=reasons,
                    missing_items=_norm_list(deep.get('missing_items')),
                    optimization_points=_norm_list(deep.get('optimization_points')),
                    highlight=deep.get('highlight'))
        except (ValueError, OSError, AttributeError):
            pass

    return index


# ==================== 读取侧视图 ====================

def load_jobs(run_dir):
    """qualified_jobs.json → list。被包一层（{"jobs": [...]}）时自动解开。"""
    path = os.path.join(run_dir, 'qualified_jobs.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    return data


def build_ranked(run_dir, limit=None):
    """qualified_jobs.json × build_match_index → 一张可直接读的表。

    `index` 是 **1-based 序号**，和 `--only` / `materials_*_N` / `apply --max`
    用的是同一个序号（即 qualified_jobs.json 里的原始顺序），所以这张表可以直接
    当作「选哪几个」的依据，不需要再换算。

    注意**不排序**：顺序就是 qualified_jobs.json 的顺序。想按分数看自己排 ——
    这里一排序，index 和下游产物的对应关系就断了。
    """
    jobs = load_jobs(run_dir)
    index = build_match_index(run_dir)

    rows = []
    for i, job in enumerate(jobs, 1):
        if limit and i > limit:
            break
        link = job.get('link') or ''
        match = index.get(link) or {}
        row = {
            'index': i,
            'position': job.get('职位') or job.get('position') or '',
            'company': job.get('公司') or job.get('company') or '',
            'city': job.get('城市') or job.get('city') or '',
            'salary': job.get('薪资') or job.get('salary') or '',
            # 分数/判定优先取合并索引（深度分最权威），索引里没有再退回岗位自带的。
            'match_score': match.get('match_score') or job.get('match_score'),
            'category': match.get('category') or job.get('application_category') or '',
            'link': link,
        }
        # 缺失项和亮点只报条数 + 首条，正文留给 --kind deep / 材料本身。
        missing = match.get('missing_items') or _norm_list(job.get('missing_items'))
        if missing:
            row['missing_items'] = missing[:3]
        highlight = match.get('highlight') or job.get('highlight')
        if highlight:
            row['highlight'] = str(highlight)[:80]
        rows.append(row)

    matched = sum(1 for r in rows if r.get('match_score') is not None)
    return {
        'total': len(rows),
        # matched < total 说明有岗位没在任何匹配产物里出现过（多半是只跑了 crawl，
        # 或者 link 变了）。把它报出来，免得读的人以为「分数就是空的」。
        'matched': matched,
        'jobs': rows,
    }
