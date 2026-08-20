# -*- coding: utf-8 -*-
"""岗位要求(权威抽取)覆盖判定打分的测试。

「岗位要求文本(detail.jd) 是唯一事实来源」——卡片标签/筛选条件不一定准。判定的
学历/经验/薪资/缺失技能应以抽取得来的权威要求为准，只有 JD 缺失或未提及时才回退
卡片标签。本测试直接打 score_job_advanced / normalize_requirements / enrich，
LLM 抽取本身不跑（那是 run_requirements 的事，这里只验评分侧怎么消费 _jd_req）。

跑法：python tests/test_scoring_jd_req.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import resume_matcher.requirements as REQ_MOD  # noqa: E402
from resume_matcher.requirements import (  # noqa: E402
    enrich, normalize_requirements, save_all, load_all,
)
from resume_matcher.scoring import (
    score_job_advanced,
    CATEGORY_CANNOT_APPLY, CATEGORY_NEED_OPTIMIZATION, CATEGORY_QUALIFIED,
)


def base_job(**over):
    """岗位 dict 用真实 CSV 中文字段构造；缺省给一套「卡片与真实要求一致」的基线。"""
    job = {
        'link': 'https://www.zhipin.com/job_detail/req99999.html',
        '职位': '后端开发',
        '薪资': '15-25K',
        '经验': '经验不限',
        '学历': '本科',
        '技能标签': 'Python Golang',
        '岗位要求和职责': '负责后端服务开发，使用 Python 与 Go',
        '公司': '测试公司',
    }
    job.update(over)
    return job


BASE_KW = dict(
    resume_skills=['Python'],
    resume_keywords=['后端'],
    salary_min=15,
    salary_max=20,
    user_experience_years=1,
    user_degree='大专',   # 等级 3；本科=4
)


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:300]))
        if not cond:
            failures.append(label)

    # ================================================================
    print('=== 1. normalize_requirements：形状收紧，未知键丢弃 ===')
    check('非 dict 输入 → 全空',
          normalize_requirements(None) ==
          {'学历要求': '', '经验要求': '', '薪资范围': '', '技能要求': []})
    r = normalize_requirements({'学历要求': '本科及以上', '经验要求': '3年以上',
                                '薪资范围': '面议', '技能要求': 'Kubernetes, Docker',
                                '自造字段': 'x'})
    check('中文字段进对应键', r['学历要求'] == '本科及以上' and r['薪资范围'] == '面议', r)
    check('技能字符串(逗号/顿号)切列表', r['技能要求'] == ['Kubernetes', 'Docker'], r)
    check('未知键被丢弃', '自造字段' not in r, r)
    r2 = normalize_requirements({'技能要求': ['a', None, '  ', 'b'] * 3})
    check('技能列表剔空白/None 且 ≤8',
          r2['技能要求'] == ['a', 'b', 'a', 'b', 'a', 'b'], r2['技能要求'])
    check('文本字段剥首尾空白', normalize_requirements({'经验要求': '  2年以上 '})['经验要求'] == '2年以上')

    # ================================================================
    print('\n=== 2. 经验：卡片标 5-10 年，岗位要求说不限 → 以岗位要求为准 ===')
    card = base_job(经验='5-10年', 学历='大专')   # 学历不设门槛，只试经验的权威覆盖
    no_req = score_job_advanced(card, **BASE_KW)
    check('无权威覆盖：按卡片 5-10年 对 1 年 → 判不可投（经验差）',
          no_req['application_category'] == CATEGORY_CANNOT_APPLY,
          (no_req['application_category'], no_req['application_category_reason']))

    with_req = score_job_advanced(
        {**card, '_jd_req': {'经验要求': '经验不限'}}, **BASE_KW)
    check('有权威覆盖：岗位要求"经验不限" → 经验满分',
          with_req['experience_score'] == 20, with_req['experience_score'])
    check('并撤销"经验龄差距"的不可投裁定',
          with_req['application_category'] != CATEGORY_CANNOT_APPLY,
          (with_req['application_category'], with_req['application_category_reason']))

    # ================================================================
    print('\n=== 3. 学历：卡片 大专，岗位要求 本科及以上 → 以岗位要求为准 ===')
    low = base_job(学历='大专')
    card_only = score_job_advanced(low, **BASE_KW)
    check('无权威覆盖：按卡片大专对简历大专 → 不拦投',
          card_only['application_category'] != CATEGORY_CANNOT_APPLY,
          card_only['application_category'])

    steeper = score_job_advanced(
        {**low, '_jd_req': {'学历要求': '本科及以上'}}, **BASE_KW)
    check('有权威覆盖：真实要求本科 → 简历大专判不可投（学历硬门槛）',
          steeper['application_category'] == CATEGORY_CANNOT_APPLY
          and '学历' in steeper['application_category_reason'],
          (steeper['application_category'], steeper['application_category_reason']))
    check('反向：真实要求大专、卡片硕士 → 学历门槛放宽放行',
          score_job_advanced({**low, '学历': '硕士',
                              '_jd_req': {'学历要求': '大专'}}, **BASE_KW)[
              'application_category'] != CATEGORY_CANNOT_APPLY)

    # ================================================================
    print('\n=== 4. 薪资：岗位要求"面议" → 视为薪资未知，不触发自动投递 ===')
    paid = base_job(薪资='15-25K')
    card_sal = score_job_advanced(paid, **BASE_KW)
    check('无权威覆盖：卡片 15-25K 对期望 15-20 → 薪资匹配(20 分)',
          card_sal['salary_score'] == 20 and card_sal['parsed_salary_low'] == 15,
          (card_sal['salary_score'], card_sal['parsed_salary_low']))

    face = score_job_advanced({**paid, '_jd_req': {'薪资范围': '面议'}}, **BASE_KW)
    check('有权威覆盖：岗位要求"面议" → 薪资未知(10 分)、解析成 0',
          face['salary_score'] == 10 and face['parsed_salary_low'] == 0,
          (face['salary_score'], face['parsed_salary_low']))
    check('面议不误判 qualified（未经验证的薪资不应触发自动投递）',
          face['application_category'] != CATEGORY_QUALIFIED,
          face['application_category'])

    # ================================================================
    print('\n=== 5. 缺失技能：以岗位要求抽出的技能为准，而非卡片标签 ===')
    j = base_job(岗位要求和职责='', 技能标签='Python Golang')
    from_card = score_job_advanced(j, **BASE_KW)
    check('无权威覆盖：卡片缺 Golang → missing 从卡片算',
          from_card['missing_skills'] == ['Golang'], from_card['missing_skills'])

    from_req = score_job_advanced(
        {**j, '_jd_req': {'技能要求': ['Kubernetes', 'Docker'], '学历要求': '', '经验要求': '',
                          '薪资范围': ''}}, **BASE_KW)
    check('有权威覆盖：JD 要求 K8s/Docker → missing 从岗位要求算',
          from_req['missing_skills'] == ['Kubernetes', 'Docker'],
          from_req['missing_skills'])

    # ================================================================
    print('\n=== 6. enrich：按 link 挂缓存，未命中/无 link 不动 ===')
    jobs = [{'link': 'https://a/1.html', '岗位要求和职责': 'x'},
            {'link': 'https://b/2.html', '岗位要求和职责': 'y'},
            {'link': '', '岗位要求和职责': 'z'}]
    cached = {'https://a/1.html': {'学历要求': '本科'}}
    n = enrich(jobs, cached=cached, quiet=True)
    check('命中 1 个', n == 1, n)
    check('命中者挂上 _jd_req', jobs[0]['_jd_req'] == {'学历要求': '本科'}, jobs[0])
    check('未命中者不动', '_jd_req' not in jobs[1], jobs[1].keys())
    check('无 link 者不动', '_jd_req' not in jobs[2], jobs[2].keys())

    # ================================================================
    print('\n=== 7. 缓存读写：save_all/load_all 原子往返 ===')
    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix='req_test_')
    orig = REQ_MOD.cache_path
    REQ_MOD.cache_path = lambda: os.path.join(tmp, 'job_requirements.json')
    try:
        save_all({'l1': {'学历要求': '本科'}, 'l2': {'经验要求': '经验不限'}})
        check('load_all 读回', load_all()['l1'] == {'学历要求': '本科'}, load_all())
        os.remove(REQ_MOD.cache_path())
        check('缺失文件返回空', not load_all())
    finally:
        REQ_MOD.cache_path = orig
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ 岗位要求权威覆盖测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())