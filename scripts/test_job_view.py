"""
岗位视图字段一致性回归测试。

背景：岗位从 CSV（中文列名）走到前端（ASCII 键）路上要被重新构造一次。这里曾经
有三份各自独立的白名单（scoring / report / deep_analysis），服务三条不同路径，
加字段必须同步改三处 —— 漏一处那条路径就静默丢字段，不报错，前端只看到空值。
HR 活跃度就这么丢过一次：JSON 正常，HTML 每张卡都显示「未采集」，而数据已经采到了。

现在字段枚举只有 scoring.build_job_view 一处。这个测试锁死那个约束：
两个产出（HTML 内嵌 JSON / job_classification.json）× 两种模式，
字段集必须完全一致，且都必须等于 JOB_VIEW_FIELDS。

用法: python scripts/test_job_view.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resume_matcher import (  # noqa: E402
    JOB_VIEW_FIELDS,
    ResumeProfile,
    build_job_view,
    classify_jobs_advanced,
    generate_bauhaus_json,
    generate_html_report,
    hr_activity_rank,
    tiers_to_classification,
)

FAILURES = []


def check(label, cond, detail=''):
    print(('  [OK]   ' if cond else '  [FAIL] ') + label + (f' — {detail}' if detail else ''))
    if not cond:
        FAILURES.append(label)


def walk_jobs(obj):
    """递归找出所有岗位视图 dict（以 hr_activity_rank 存在为标志）。"""
    if isinstance(obj, dict):
        if 'hr_activity_rank' in obj:
            yield obj
        for v in obj.values():
            yield from walk_jobs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_jobs(v)


def embedded_json(html):
    """取出 HTML 报告里内嵌的数据块 —— 前端真正读到的东西。"""
    m = re.search(r'(?:const|var|let)\s+\w*(?:DATA|data)\w*\s*=\s*(\{.*?\});\s*\n', html, re.S)
    return json.loads(m.group(1)) if m else None


def make_profile():
    return ResumeProfile(
        basic_info={'name': '测试'},
        education={'school': 'X大学', 'degree': '本科', 'major': '计算机', 'graduation_year': '2020'},
        skills={'languages': ['Python'], 'frameworks': ['FastAPI'], 'databases': ['MySQL']},
        experience={'total_years': 3, 'positions': [{'title': 'Python后端开发'}]},
        salary_expectation={'min': 15, 'max': 25},
    )


def make_rows():
    """四行，覆盖 rank 的四种形态：在线 / 高活跃 / 低活跃 / 未采集。"""
    base = {
        'link': '', '职位': 'Python后端开发', '城市': '西安', '区域': '雁塔区', '商圈': '',
        '公司': '', '薪资': '15-25K', '经验': '3-5年', '学历': '本科',
        '领域': '互联网', '性质': '已上市', '规模': '100-499人',
        '技能标签': 'Python,FastAPI,MySQL', '福利标签': '五险一金', '位置': '西安',
        '岗位要求和职责': 'Python 后端开发，熟悉 FastAPI 与 MySQL。' * 30,
        '公司信息': '一家公司。' * 60,
    }
    specs = [
        ('甲公司', '刚刚活跃', '是', '招聘经理'),
        ('乙公司', '今日活跃', '否', 'HR'),
        ('丙公司', '本月活跃', '否', '猎头顾问'),
        ('丁公司', '', '', ''),          # 无 -d，未采集
    ]
    rows = []
    for i, (co, act, on, title) in enumerate(specs):
        r = dict(base)
        r['公司'] = co
        r['link'] = f'https://www.zhipin.com/job_detail/testjob{i:05d}A.html'
        r['HR活跃度'], r['HR在线'], r['HR职位'] = act, on, title
        rows.append(r)
    return rows


def main():
    print('\n=== 1. build_job_view 是唯一字段来源 ===')
    empty = build_job_view({})
    check('空 dict 也返回完整字段集', set(empty) == set(JOB_VIEW_FIELDS),
          f'{len(empty)} 字段')
    check('未采集时 rank 为 None', empty['hr_activity_rank'] is None)
    check('overrides 生效', build_job_view({}, overrides={'match_score': 99})['match_score'] == 99)
    check('截断长度可调',
          len(build_job_view({'岗位要求和职责': 'x' * 2000}, jd_len=500)['jd']) == 500)

    print('\n=== 2. rank 分档单调（在线 > 今日 > 本月，未采集为 None）===')
    ranks = [hr_activity_rank(r) for r in make_rows()]
    print(f'  ranks = {ranks}')
    check('前三档严格递减', ranks[0] > ranks[1] > ranks[2],
          f'{ranks[0]} > {ranks[1]} > {ranks[2]}')
    check('未采集为 None（不是 0）', ranks[3] is None)

    print('\n=== 3. 两条产出路径字段集一致 ===')
    profile, rows = make_profile(), make_rows()
    t1, t2, t3, t4 = classify_jobs_advanced(profile, rows)[:4]
    out = tempfile.mkdtemp(prefix='jobview_')
    generate_bauhaus_json(profile, t1, t2, t3, tier4=t4, output_dir=out)
    generate_html_report(profile, tiers_to_classification(t1, t2, t3), output_dir=out)

    with open(os.path.join(out, 'job_classification.json'), encoding='utf-8') as f:
        json_jobs = list(walk_jobs(json.load(f)))
    with open(os.path.join(out, 'matching_report.html'), encoding='utf-8') as f:
        html = f.read()
    html_jobs = list(walk_jobs(embedded_json(html) or {}))

    check('JSON 有岗位', len(json_jobs) > 0, f'{len(json_jobs)} 个')
    check('HTML 内嵌数据有岗位', len(html_jobs) > 0, f'{len(html_jobs)} 个')

    for label, jobs in (('JSON', json_jobs), ('HTML', html_jobs)):
        for j in jobs:
            missing = set(JOB_VIEW_FIELDS) - set(j)
            if missing:
                check(f'{label} 岗位字段完整', False, f'缺 {sorted(missing)}')
                break
        else:
            check(f'{label} 每个岗位都有全部 {len(JOB_VIEW_FIELDS)} 个字段', True)

    if json_jobs and html_jobs:
        check('两条路径字段集完全一致', set(json_jobs[0]) == set(html_jobs[0]),
              f'差异 {set(json_jobs[0]) ^ set(html_jobs[0])}')

    print('\n=== 4. 采集到的活跃度不会被显示成未采集 ===')
    for label, jobs in (('JSON', json_jobs), ('HTML', html_jobs)):
        collected = [j for j in jobs if j['hr_active_desc']]
        bad = [j for j in collected if j['hr_activity_rank'] is None]
        check(f'{label} 有 desc 的岗位 rank 均非 None', not bad,
              f'{len(collected)} 个已采集，{len(bad)} 个被误判为未采集')

    print('\n=== 5. 无 -d 场景：全部退化为未采集 ===')
    blank = [dict(r, **{'HR活跃度': '', 'HR在线': '', 'HR职位': ''}) for r in make_rows()]
    b1, b2, b3, b4 = classify_jobs_advanced(profile, blank)[:4]
    out2 = tempfile.mkdtemp(prefix='jobview_blank_')
    generate_bauhaus_json(profile, b1, b2, b3, tier4=b4, output_dir=out2)
    generate_html_report(profile, tiers_to_classification(b1, b2, b3), output_dir=out2)
    with open(os.path.join(out2, 'job_classification.json'), encoding='utf-8') as f:
        bj = list(walk_jobs(json.load(f)))
    with open(os.path.join(out2, 'matching_report.html'), encoding='utf-8') as f:
        bh = list(walk_jobs(embedded_json(f.read()) or {}))
    check('JSON 全部 rank=None', bj and all(j['hr_activity_rank'] is None for j in bj))
    check('HTML 全部 rank=None', bh and all(j['hr_activity_rank'] is None for j in bh))

    print('\n' + '=' * 46)
    if FAILURES:
        print(f'FAILED: {len(FAILURES)} 项')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('ALL PASSED')
    return 0


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):   # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
