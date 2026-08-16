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


def main():
    if len(sys.argv) < 3 or '--kind' not in sys.argv:
        print('Usage: python scripts/read_thin.py <file> --kind {jobs|profile|deep}')
        print()
        print('Kinds:')
        print('  jobs     — qualified_jobs.json: strip full JDs and company info')
        print('  profile  — profile.json: strip full experience/project text and PII')
        print('  deep     — deep_results.json: verdicts only, no full JD text')
        sys.exit(1)

    file_path = None
    kind = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--kind':
            kind = sys.argv[i + 1]
            i += 2
        else:
            file_path = sys.argv[i]
            i += 1

    if not file_path or not os.path.isfile(file_path):
        print(f'ERROR: file not found: {file_path}')
        sys.exit(1)

    if kind not in KINDS:
        print(f'ERROR: unknown kind "{kind}". Valid: {", ".join(KINDS.keys())}')
        sys.exit(1)

    try:
        with open(file_path, encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f'ERROR: cannot read {file_path}: {e}')
        sys.exit(1)

    result = KINDS[kind](data)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()