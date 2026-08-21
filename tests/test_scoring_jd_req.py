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
    parse_degree_level,
    CATEGORY_CANNOT_APPLY, CATEGORY_NEED_OPTIMIZATION, CATEGORY_QUALIFIED,
)
from resume_matcher.utils import parse_experience_years


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
    print('\n=== 5b. 学历名校/批次门槛不能当"不限"，或档取下限 ===')
    check('双一流以上学历(无学历词) → 本科底线 4，而非 0',
          parse_degree_level('公办全日制双一流以上学历') == 4,
          parse_degree_level('公办全日制双一流以上学历'))
    check('985/211 + 本科 → 仍本科 4',
          parse_degree_level('本科及以上，985/211双一流大学毕业') == 4,
          parse_degree_level('本科及以上，985/211双一流大学毕业'))
    check('统招二本及以上 → 本科 4，而非不限 0',
          parse_degree_level('统招二本及以上') == 4,
          parse_degree_level('统招二本及以上'))
    check('大专或本科 → 取下限大专 3',
          parse_degree_level('大专或本科') == 3, parse_degree_level('大专或本科'))
    check('常规 本科/硕士 不受影响',
          parse_degree_level('本科') == 4 and parse_degree_level('硕士及以上') == 5,
          (parse_degree_level('本科'), parse_degree_level('硕士及以上')))
    # 打分侧：985 岗现在能拦大专，不再误放行
    gate = base_job(学历='大专', _jd_req={'学历要求': '本科及以上，985/211双一流大学毕业'})
    g = score_job_advanced(gate, **BASE_KW)   # 简历大专
    check('985/211 岗拦大专 → 判不可投', g['application_category'] == CATEGORY_CANNOT_APPLY,
          (g['application_category'], g['application_category_reason']))

    def _premium(user_degree='本科', user_school=''):
        return score_job_advanced(
            base_job(学历='本科', _jd_req={'学历要求': '本科及以上，985/211双一流大学毕业'}),
            resume_skills=['Java'], resume_keywords=['后端'],
            salary_min=15, salary_max=20, user_experience_years=2,
            user_degree=user_degree, user_school=user_school)

    check('985 名校(北京大学) 通过名校门槛',
          _premium(user_school='北京大学')['application_category'] != CATEGORY_CANNOT_APPLY,
          _premium(user_school='北京大学')['application_category'])
    check('非名校(职业技术学院) 被名校门槛拦下',
          _premium(user_school='某职业技术学院')['application_category'] == CATEGORY_CANNOT_APPLY,
          _premium(user_school='某职业技术学院')['application_category'])
    check('缩写/限定名(清华) 也能命中名校',
          _premium(user_school='清华大学深圳研究院')['application_category'] != CATEGORY_CANNOT_APPLY)
    check('学校缺失时名校门槛不误杀（退回学历等级）',
          _premium(user_school='')['application_category'] != CATEGORY_CANNOT_APPLY)
    # 非名校门槛的普通 JD：非名校也放行
    plain = score_job_advanced(
        base_job(学历='本科', _jd_req={'学历要求': '本科及以上'}),
        resume_skills=['Java'], resume_keywords=['后端'], salary_min=15, salary_max=20,
        user_experience_years=2, user_degree='本科', user_school='某职业技术学院')
    check('非名校门槛岗：非名校不被拦',
          plain['application_category'] != CATEGORY_CANNOT_APPLY,
          plain['application_category'])

    # ================================================================
    print('\n=== 6. parse_experience_years：子年形式必须化解为小数，不能回退裸数字 ===')
    from resume_matcher.utils import parse_experience_years as _py
    check('半年以上 → 0.5', _py('半年以上') == 0.5, _py('半年以上'))
    check('半年 → 0.5', _py('半年') == 0.5, _py('半年'))
    check('0.5年及以上 → 0.5（而非被错抓成 5）', _py('0.5年及以上') == 0.5, _py('0.5年及以上'))
    check('1.5年 → 1.5', _py('1.5年以上') == 1.5, _py('1.5年以上'))
    check('6个月 → 0.5', _py('6个月') == 0.5, _py('6个月'))
    check('3个月 → 0.25', _py('3个月') == 0.25, _py('3个月'))
    check('整数年仍取上限：3-5年 → 5', _py('3-5年') == 5, _py('3-5年'))
    check('整数年：3年以上 → 3', _py('3年以上') == 3, _py('3年以上'))
    check('经验不限 → 0', _py('经验不限') == 0, _py('经验不限'))
    check('空串 → 0', _py('') == 0)

    # ----- 打分侧：双档经验要求按学历分流；多年/有相关经验按语义化 -----
    dual = base_job(**{'_jd_req': {'经验要求': '专科5年以上，本科3年以上'}})
    ben = score_job_advanced(dual, resume_skills=['Java'], resume_keywords=['后端'],
                             salary_min=15, salary_max=20,
                             user_experience_years=3, user_degree='本科')
    check('本科+3年 → 按本科档(3年) → 经验满分(20)',
          ben['experience_score'] == 20, (ben['experience_score'],))

    duanzhuan = score_job_advanced(dual, resume_skills=['Java'], resume_keywords=['后端'],
                                   salary_min=15, salary_max=20,
                                   user_experience_years=4, user_degree='专科')
    check('专科+4年 → 按专科档(5年) → 经验略低而非满分',
          duanzhuan['experience_score'] < 20, duanzhuan['experience_score'])

    vague = score_job_advanced(base_job(**{'_jd_req': {'经验要求': '有相关经验'}}),
                               **BASE_KW)   # 用户 1 年
    check('有相关经验 → 视为宽松(满分)，不再吃兜底 8 分',
          vague['experience_score'] == 20, vague['experience_score'])

    duonian = score_job_advanced(base_job(**{'_jd_req': {'经验要求': '多年'}}),
                                 resume_skills=['Java'], resume_keywords=['后端'],
                                 salary_min=15, salary_max=20,
                                 user_experience_years=4, user_degree='本科')
    check('多年 → 解析成 3 年，4 年经验 → 经验满分',
          duonian['experience_score'] == 20, (duonian['experience_score'],))
    check('多年不再走兜底 8 分', duonian['experience_score'] != 8, duonian['experience_score'])

    # ================================================================
    print('\n=== 5c. 技能里 A/B、A或B 二选一 → 命中其一即不算缺 ===')
    j_dup = {**base_job(), '_jd_req': {'学历要求': '', '经验要求': '', '薪资范围': '',
                                      '技能要求': ['MySQL/PostgreSQL', 'Vue/React']}}
    only_mysql = score_job_advanced(j_dup, resume_skills=['MySQL', 'Vue'],
                                    resume_keywords=['后端'], salary_min=15, salary_max=20,
                                    user_experience_years=1, user_degree='大专')
    check('会 MySQL 即满足"MySQL/PostgreSQL"，不再报缺', only_mysql['missing_skills'] == [],
          only_mysql['missing_skills'])
    required = score_job_advanced({**base_job(), '_jd_req': {
        '学历要求': '', '经验要求': '', '薪资范围': '', '技能要求': ['Kubernetes']}},
        resume_skills=['MySQL'], resume_keywords=['后端'], salary_min=15, salary_max=20,
        user_experience_years=1, user_degree='大专')
    check('无斜杠项仍照常报缺', required['missing_skills'] == ['Kubernetes'],
          required['missing_skills'])

    # ----- 打分侧：低门槛"半年以上"应给满分而非兜底 8 分 -----
    fresh = base_job(**{'_jd_req': {'经验要求': '半年以上'}})
    fresh['经验'] = '经验不限'  # 卡片无关紧要，判定以权威要求为准
    half = score_job_advanced(fresh, **BASE_KW)  # 用户 1 年经验，远超半年门槛
    check('用户 1 年 ≥ 半年要求 → 经验满分(20)',
          half['experience_score'] == 20, half['experience_score'])
    check('不再落入兜底 8 分', half.get('experience_score', 8) != 8,
          half['experience_score'])

    # ----- 打分侧：0.5年及以上不应再把 JD 当 5 年门槛 -----
    sub = base_job(**{'_jd_req': {'经验要求': '0.5年及以上'}})
    sub2 = score_job_advanced(sub, **BASE_KW)  # 用户 1 年
    check('用户 1 年 ≥ 0.5年要求 → 经验满分(20)',
          sub2['experience_score'] == 20, sub2['experience_score'])

    # ================================================================
    print('\n=== 7. enrich：按 link 挂缓存，未命中/无 link 不动 ===')
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
    print('\n=== 8. 缓存读写：save_all/load_all 原子往返 ===')
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