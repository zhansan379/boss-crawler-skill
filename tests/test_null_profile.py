#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试：profile.json 里字段值为 null 时不该崩。

2026-08-13 实测事故：应届简历没写期望薪资，解析产出
`"salary_expectation": {"min": null, "max": null}`——键存在、值是 None，
于是 `salary.get('min', 8)` 的默认值形同虚设，None 一路传到
`score_job_advanced()` 的 `sal_low <= salary_max`，抛
`TypeError: '<=' not supported between 'int' and 'NoneType'`，
整批 45 个岗位的评分挂掉，重跑两次才绕过。

同一类坑还有 `experience.total_years`（`None > 0`）、报告生成里的
`profile.salary_expectation.get(...)`（整个字段可能是 null），以及
`skills.get('tools', [])`（某类技能为空时值是 null，`extend(None)` 崩）。

结论：凡是从 JSON 读出来的值，兜底一律用 `or`，不要用 `get` 的第二参数。

用法：python tests/test_null_profile.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

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

    # 场景 3：技能分类的值是 null —— 简历里没有该类技能时解析就这么产出。
    # 2026-08-13 由深度分析分片的端到端测试抓到：`skills.get(cat, [])` 返回
    # None，`extend(None)` / `None + list` 在 report.py:56、report.py:188、
    # scoring.py:661、auto_apply.py:106 四处 TypeError，报告整个生不出来。
    print('\n[3] skills 某些分类的值是 null')
    p3 = ResumeProfile(
        basic_info={'name': '王五', 'city': '杭州', 'target_position': '后端'},
        education={'degree': '本科', 'major': '计算机', 'school': 'X大学'},
        experience={'total_years': 3},
        skills={'programming': ['Python'], 'frameworks': None,
                'tools': None, 'other': []},
        salary_expectation={'min': 20, 'max': 30},
        keywords=['Python'],
    )
    try:
        tiers = classify_jobs_advanced(p3, JOBS)
        check('classify_jobs_advanced 不抛异常', sum(len(t) for t in tiers) == 1)
    except TypeError as error:
        check('classify_jobs_advanced 不抛异常 (%s)' % error, False)

    try:
        greeting = generate_greeting(p3, JOBS[0])
        check('generate_greeting 不抛异常', bool(greeting))
    except TypeError as error:
        check('generate_greeting 不抛异常 (%s)' % error, False)

    # 报告生成是这一类坑最容易漏掉的一环：它在流程末尾，前面全跑完才崩
    import tempfile
    from resume_matcher.report import generate_html_report, generate_bauhaus_json
    from resume_matcher.scoring import tiers_to_classification
    tmp_dir = tempfile.mkdtemp(prefix='null_profile_test_')
    try:
        # tiers_to_classification 只吃前三层，tier4 是丢弃的
        tier1, tier2, tier3, _tier4 = classify_jobs_advanced(p3, JOBS)
        classification = tiers_to_classification(tier1, tier2, tier3)
        try:
            path = generate_html_report(p3, classification, output_dir=tmp_dir)
            check('generate_html_report 不抛异常', os.path.getsize(path) > 0)
        except TypeError as error:
            check('generate_html_report 不抛异常 (%s)' % error, False)
        try:
            # 这个函数吃的是分层列表，不是 JobClassification
            generate_bauhaus_json(p3, tier1, tier2, tier3, _tier4,
                                  output_dir=tmp_dir)
            check('generate_bauhaus_json 不抛异常', True)
        except TypeError as error:
            check('generate_bauhaus_json 不抛异常 (%s)' % error, False)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

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
