#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Extract only the "thin" fields from large JSON files.

The main agent must never Read full qualified_jobs.json, profile.json, or
deep_results.json — those files contain full JDs, company profiles, and
detailed experience descriptions that bloat the main context. This script
extracts just the fields the main agent actually needs (link, company,
position, score, verdicts) and prints them as compact JSON.

Usage:
  python scripts/read_thin.py <file> --kind jobs      # qualified_jobs.json
  python scripts/read_thin.py <file> --kind profile   # profile.json
  python scripts/read_thin.py <file> --kind deep      # deep_results.json
  python scripts/read_thin.py <run_dir> --kind ranked # 序号+公司+职位+分数+判定 一张表

`ranked` 吃的是 **run 目录**而不是单个文件，因为「分数 + 判定 + 公司 + 职位」
本来就散在 4 个文件里（qualified_jobs / scored_jobs / job_classification /
deep_results+deep_candidates）。合并逻辑在 match_index.py，与 gen_materials
共用同一份 —— 别在这里再拼一次。
"""

import json, os, sys

# Windows console defaults to GBK; ensure UTF-8 output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError, OSError):
        pass


def thin_jobs(data: list) -> list:
    """Extract thin fields from each job entry — no full JDs or company info."""
    thin_fields = [
        'link', '职位', '公司', '城市', '薪资',
        # 这四列是投递决策信号，不是明细：已失效=是 投了白费，代招=是 或 HR公司≠公司
        # 说明联系人不是用人方 HR，HR活跃度决定 apply 的排序。缺列（未采集）时下面的
        # `if k in job` 会整个跳过 —— 空字符串和「否」不是一回事，别在这里补默认值。
        '已失效', '代招', 'HR公司', 'HR活跃度',
        'match_score', 'difficulty',
        'application_category', 'application_category_reason',
        'match_reasons', 'matched_skills', 'missing_items',
        'highlight', 'risk', 'optimization_points',
    ]
    out = []
    for job in data:
        entry = {}
        for k in thin_fields:
            if k in job:
                entry[k] = job[k]
        out.append(entry)
    return out


def thin_profile(data: dict) -> dict:
    """Extract summary fields from profile — no full experience/project text."""
    out = {}

    # Education: summary only
    edu = data.get('education', {})
    if edu:
        out['education'] = {
            'school': edu.get('school', ''),
            'degree': edu.get('degree', ''),
            'major': edu.get('major', ''),
            'graduation_year': edu.get('graduation_year', ''),
        }

    # Basic info: only the fields needed for matching/crawl — no PII
    bi = data.get('basic_info', {})
    out['basic_info'] = {
        'expected_city': bi.get('expected_city', ''),
        'expected_position': bi.get('expected_position', ''),
        'availability': bi.get('availability', ''),
    }

    # Skills: dict of category → list of skill names, plus a 'summary' string
    skills = data.get('skills', {})
    if isinstance(skills, dict):
        skill_names = []
        for cat, items in skills.items():
            if cat == 'summary':
                continue
            if isinstance(items, list):
                skill_names.extend(items)
        out['skills'] = {
            'count': len(skill_names),
            'names': skill_names,
        }
    else:
        out['skills'] = {'count': 0, 'names': []}

    # Experience: dict with 'total_years' and 'companies' list
    experience = data.get('experience', {})
    if isinstance(experience, dict):
        companies = experience.get('companies', [])
        out['experience'] = {
            'total_years': experience.get('total_years', ''),
            'count': len(companies),
            'companies': [
                c.get('name', c.get('organization', '?'))
                if isinstance(c, dict) else '?'
                for c in companies
            ],
        }
    elif isinstance(experience, list):
        # Fallback: list format
        out['experience'] = {
            'count': len(experience),
            'companies': [
                e.get('company', e.get('organization', '?'))
                if isinstance(e, dict) else '?'
                for e in experience
            ],
        }
    else:
        out['experience'] = {'count': 0, 'companies': []}

    # Projects: count only (no full descriptions)
    projects = data.get('projects', [])
    out['projects'] = {'count': len(projects)}
    if projects:
        out['projects']['names'] = [
            p.get('name', p.get('title', '?'))
            if isinstance(p, dict) else '?'
            for p in projects
        ]

    # Awards: count + names
    awards = data.get('awards', [])
    out['awards'] = {
        'count': len(awards),
        'names': [a.get('name', str(a)) if isinstance(a, dict) else str(a) for a in awards],
    }

    # Salary expectation
    out['salary_expectation'] = data.get('salary_expectation', {})

    # Keywords
    out['keywords'] = data.get('keywords', [])

    return out


def thin_deep(data: dict) -> list:
    """Extract thin fields from deep_results — verdicts only, no full JD text."""
    results = data.get('results', [])
    thin_fields = [
        'rank', 'score', 'category', 'reason',
        'missing_items', 'optimization_points', 'highlight', 'risk',
        'education_match', 'experience_match', 'skills_match', 'salary_match',
    ]
    out = []
    for r in results:
        entry = {}
        for k in thin_fields:
            if k in r:
                entry[k] = r[k]
        out.append(entry)
    return out


KINDS = {
    'jobs': thin_jobs,
    'profile': thin_profile,
    'deep': thin_deep,
}

# ranked 不在 KINDS 里：它的入参是 run 目录，不是一个 JSON 文件，走另一条分支。
DIR_KINDS = ('ranked',)

USAGE = 'Usage: python scripts/read_thin.py <file|run_dir> --kind {jobs|profile|deep|ranked}'


def main():
    argv = sys.argv[1:]
    if len(argv) < 2 or '--kind' not in argv:
        print(USAGE)
        print()
        print('Kinds:')
        print('  jobs     — qualified_jobs.json: strip full JDs and company info')
        print('  profile  — profile.json: strip full experience/project text and PII')
        print('  deep     — deep_results.json: verdicts only, no full JD text')
        print('  ranked   — <run_dir>: 1-based index + company + position + score + verdict,')
        print('             joined across scored_jobs / job_classification / deep_results')
        sys.exit(1)

    target = None
    kind = None
    limit = None
    i = 0
    while i < len(argv):
        if argv[i] == '--kind':
            kind = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif argv[i] == '--limit':
            raw = argv[i + 1] if i + 1 < len(argv) else ''
            try:
                limit = int(raw)
            except ValueError:
                print(f'ERROR: --limit wants an integer, got "{raw}"')
                sys.exit(2)
            i += 2
        else:
            target = argv[i]
            i += 1

    if kind not in KINDS and kind not in DIR_KINDS:
        valid = ', '.join(list(KINDS) + list(DIR_KINDS))
        print(f'ERROR: unknown kind "{kind}". Valid: {valid}')
        sys.exit(2)

    if kind in DIR_KINDS:
        if not target or not os.path.isdir(target):
            print(f'ERROR: --kind {kind} wants a run directory, got: {target}')
            sys.exit(1)
        import match_index
        try:
            result = match_index.build_ranked(target, limit=limit)
        except (json.JSONDecodeError, OSError) as e:
            # qualified_jobs.json 是这张表的骨架，读不到就没有序号可言。
            print(f'ERROR: cannot build ranked view from {target}: {e}')
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not target or not os.path.isfile(target):
        print(f'ERROR: file not found: {target}')
        sys.exit(1)

    try:
        with open(target, encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f'ERROR: cannot read {target}: {e}')
        sys.exit(1)

    result = KINDS[kind](data)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()