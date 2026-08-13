#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试：profile.json 里字段值为 null 时不该崩。

2026-08-13 实测事故：应届简历没写期望薪资，解析产出
`"salary_expectation": {"min": null, "max": null}`——键存在、值是 None，
于是 `salary.get('min', 8)` 的默认值形同虚设，None 一路传到
`score_job_advanced()` 的 `sal_low <= salary_max`，抛
`TypeError: '<=' not supported between 'int' and 'NoneType'`，
整批 45 个岗位的评分挂掉，重跑两次才绕过。

同一类坑还有 `experience.total_years`（`None > 0`）和报告生成里的
`profile.salary_expectation.get(...)`（整个字段可能是 null）。

用法：python scripts/test_null_profile.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resume_matcher.config import ResumeProfile
from resume_matcher.scoring import classify_jobs_advanced
from resume_matcher.auto_apply import generate_greeting

FAILURES = []


def check(label, ok):
    print('  %s %s' % ('✅' if ok else '❌', label))
    if not ok:
        FAILURES.append(label)


JOBS = [{
    '职位': 'Python后端开发', '公司': 'XX科技', '薪资': '10-15K', '经验': '应届',
    '学历': '本科', '城市': '杭州', 'link': 'https://example.com/job/1',
    '技能标签': 'Python FastAPI', '岗位要求和职责': '负责后端接口开发',
}]


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    print('=' * 46)
    print('null 值 profile 的回归测试')
    print('=' * 46)

    # 场景 1：键都在，值是 null —— 应届简历的典型形态
    print('\n[1] salary_expectation/total_years 的值是 null')
    p = ResumeProfile(
        basic_info={'name': '张三'},
        education={'degree': '本科', 'major': '计算机', 'school': 'X大学'},
        experience={'total_years': None},
        skills={'programming': ['Python'], 'frameworks': ['FastAPI']},
        salary_expectation={'min': None, 'max': None},
        keywords=['Python', 'AI'],
    )
    try:
        tiers = classify_jobs_advanced(p, JOBS)
        check('classify_jobs_advanced 不抛异常', sum(len(t) for t in tiers) == 1)
    except TypeError as error:
        check('classify_jobs_advanced 不抛异常 (%s)' % error, False)

    try:
        greeting = generate_greeting(p, JOBS[0])
        check('generate_greeting 不抛异常', bool(greeting))
        check('招呼语带上了岗位名', 'Python后端开发' in greeting)
    except TypeError as error:
        check('generate_greeting 不抛异常 (%s)' % error, False)

    # 场景 2：salary_expectation 整个是 null
    print('\n[2] salary_expectation 整个字段是 null')
    p2 = ResumeProfile(
        basic_info={'name': '李四'}, education={}, experience={},
        skills={}, salary_expectation=None, keywords=[],
    )
    try:
        tiers = classify_jobs_advanced(p2, JOBS)
        check('classify_jobs_advanced 不抛异常', sum(len(t) for t in tiers) == 1)
    except (TypeError, AttributeError) as error:
        check('classify_jobs_advanced 不抛异常 (%s)' % error, False)

    print('\n' + '=' * 46)
    if FAILURES:
        print('FAILED: %d 项' % len(FAILURES))
        for f in FAILURES:
            print('  - %s' % f)
        return 1
    print('ALL PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
