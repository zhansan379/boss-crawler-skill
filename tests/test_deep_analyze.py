# -*- coding: utf-8 -*-
"""deep_analyze.py 的测试：产出必须能被**真实的** merge_deep_results 吃下。

这套测试的重点不是「deep_analyze 自己跑通了」，而是它和下游的接缝：

  · deep_results.json 靠 rank 回填。rank 错位 = 把 A 岗位的分析结果安到 B 岗位上，
    而且合并后一切看起来都正常（分数有、理由有、报告能出），没有任何报错。这类错
    只能靠「给每个 rank 喂可区分的分数，再检查每个岗位拿到的是自己那份」抓出来。
  · category 决定岗位进不进 qualified_jobs.json，也就是决定投不投。模型说
    cannot_apply 的岗位混进投递池，代价是一条发出去撤不回的消息。
  · 单条失败不能带崩整批：失败的候选在 merge 侧要沿用规则分类（is_deep=False），
    岗位本身不能凭空消失。

所以这里不 mock merge —— 真的调 resume_matcher.deep_analysis.merge_deep_results，
真的读它写出来的 qualified_jobs.json。只有 LLM 请求是假的（stub chat_json）。

跑法：python tests/test_deep_analyze.py
"""

import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "stages"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import deep_analyze as DA
from llm.config import LLMConfig
from resume_matcher.config import CSV_FIELDS, ResumeProfile
from resume_matcher.deep_analysis import (
    save_deep_candidates, merge_deep_results, serialize_profile,
)
from resume_matcher.paths import (
    deep_candidates_path, deep_results_path, matching_report_path,
    qualified_jobs_path, timings_path,
)
from resume_matcher.scoring import (
    CATEGORY_QUALIFIED, CATEGORY_NEED_OPTIMIZATION, CATEGORY_CANNOT_APPLY,
)

FAKE_CFG = LLMConfig(base_url='https://fake.local/v1', api_key='sk-fake',
                     model='m-test', concurrency=3, max_retries=0)


def make_profile():
    return ResumeProfile(
        basic_info={'name': '张三', 'city': '太原', 'expected_city': '西安',
                    'phone': '13800000000', 'email': 'z@example.com'},
        education={'degree': '本科', 'school': '某大学', 'major': '计算机科学与技术'},
        experience={'total_years': 1,
                    'companies': [{'name': '某科技', 'position': '后端实习',
                                   'duration': '2025.03-2025.09',
                                   'highlights': ['把订单查询 P99 从 800ms 降到 120ms']}]},
        skills={'语言': ['Python', 'Java'], '框架': ['FastAPI', 'Django'],
                'AI': ['LangChain', 'RAG']},
        projects=[{'name': '智能问答系统', 'role': '主力开发',
                   'description': '基于 RAG 的企业知识库问答',
                   'highlights': ['检索召回率提升 30%']}],
        awards=[{'name': '校级一等奖学金'}],
        publications=[], social_links={},
        salary_expectation={'min': 10, 'max': 20, 'unit': 'K'},
        keywords=['Python', 'AI应用开发', 'RAG'],
        raw_text='（原文略）',
    )


def make_tier_jobs():
    """三个岗位，rule_score 刻意分开，方便验证混合评分算在了对的那条上。"""
    return [
        {
            'link': 'https://www.zhipin.com/job_detail/aaaaaaa1.html',
            '职位': 'AI应用开发工程师', '公司': '甲公司', '城市': '西安', '区域': '雁塔区',
            '商圈': '高新', 'salary': '15-25K', 'experience': '1-3年', 'degree': '本科',
            '领域': '人工智能', '性质': 'A轮', '规模': '100-499人',
            'skill_tags': 'Python,RAG,LangChain',
            'jd': '负责 RAG 问答系统研发，要求熟悉 Python 与向量检索。' * 20,
            'company_info': '甲公司成立于 2019 年，专注企业知识库。',
            '位置': '西安市雁塔区', 'source_file': 'a.csv',
            'match_score': 90, 'difficulty': 'Easy',
            'application_category': CATEGORY_QUALIFIED,
            'match_reasons': ['技能高度匹配'], 'matched_skills': ['Python'],
            'missing_skills': [], 'optimization_points': [],
        },
        {
            'link': 'https://www.zhipin.com/job_detail/bbbbbbb2.html',
            '职位': '高级架构师', '公司': '乙公司', '城市': '西安', '区域': '碑林区',
            '商圈': '', 'salary': '40-60K', 'experience': '5-10年', 'degree': '硕士',
            '领域': '金融', '性质': '已上市', '规模': '1000-9999人',
            'skill_tags': 'Java,分布式',
            'jd': '负责核心交易系统架构设计，要求 8 年以上经验。',
            'company_info': '', '位置': '西安市碑林区', 'source_file': 'a.csv',
            'match_score': 70, 'difficulty': 'Hard',
            'application_category': CATEGORY_NEED_OPTIMIZATION,
            'match_reasons': ['城市匹配'], 'matched_skills': ['Java'],
            'missing_skills': ['分布式'], 'optimization_points': ['补充分布式经验'],
        },
        {
            'link': 'https://www.zhipin.com/job_detail/ccccccc3.html',
            '职位': 'Python开发', '公司': '丙公司', '城市': '太原', '区域': '小店区',
            '商圈': '', 'salary': '8-14K', 'experience': '经验不限', 'degree': '本科',
            '领域': '企业服务', '性质': '不需要融资', '规模': '20-99人',
            'skill_tags': 'Python,Django',
            'jd': '',                      # 故意没有 JD 全文：走「信息不足」那条分支
            'company_info': '', '位置': '太原市小店区', 'source_file': 'b.csv',
            'match_score': 60, 'difficulty': 'Medium',
            'application_category': CATEGORY_NEED_OPTIMIZATION,
            'match_reasons': ['薪资可接受'], 'matched_skills': ['Python'],
            'missing_skills': ['Django'], 'optimization_points': ['补 Django 项目'],
        },
    ]


def prepare(run_dir):
    """用**真实的** save_deep_candidates 造输入，保证 fixture 形状不是我猜的。"""
    os.makedirs(run_dir, exist_ok=True)
    profile = make_profile()
    save_deep_candidates(profile, make_tier_jobs(), [], top_n=15, output_dir=run_dir)
    with open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8') as f:
        json.dump({'basic_info': profile.basic_info}, f, ensure_ascii=False)
    return run_dir


# 按公司名回答，顺带验证提示词里真的带上了这个岗位的信息
REPLIES = {
    '甲公司': {'score': 95, 'category': 'qualified', 'reason': '技能与 JD 高度契合',
              'missing_items': [], 'optimization_points': ['突出 RAG 项目'],
              'highlight': 'RAG 经验直接对口', 'risk': ''},
    '乙公司': {'score': 40, 'category': 'cannot_apply', 'reason': '要求 8 年经验，差距过大',
              'missing_items': ['分布式架构经验'], 'optimization_points': [],
              'highlight': '', 'risk': '经验年限硬门槛'},
    '丙公司': {'score': 66, 'category': 'need_optimization', 'reason': '基本符合，需补 Django',
              'missing_items': ['Django'], 'optimization_points': ['补 Django 项目'],
              'highlight': '门槛低', 'risk': '薪资偏低'},
}


def make_stub(fail_for=(), record=None):
    """假 chat_json。fail_for 里的公司名会抛错，用来测部分失败。"""
    def stub(prompt, stage=None, run_dir=None, cfg=None, **kw):
        for company, reply in REPLIES.items():
            if company in prompt:
                if record is not None:
                    record.append((company, prompt))
                if company in fail_for:
                    raise DA.LLMError('模型返回了无法解析的内容（测试注入）')
                return dict(reply)
        raise AssertionError('提示词里认不出任何公司，说明 JD 没拼进去：%r' % prompt[:200])
    return stub


def run_main(argv, stub):
    """打补丁跑 main()，返回退出码。"""
    old_argv, old_chat, old_resolve = sys.argv, DA.chat_json, DA.resolve
    sys.argv = ['deep_analyze.py'] + argv
    DA.chat_json = stub
    DA.resolve = lambda **kw: FAKE_CFG      # 不碰开发机上真实的 llm_config.json
    try:
        return DA.main()
    finally:
        sys.argv, DA.chat_json, DA.resolve = old_argv, old_chat, old_resolve


def read_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:400]))
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp(prefix='deep_test_')
    try:
        # ================================================================
        print('=== 1. build_resume_info：给模型看的简历里不能夹带规则侧结论 ===')
        # 走一遍 serialize→JSON→反序列化，确保测的是磁盘上真实存在的那个形状
        info = DA.build_resume_info(
            json.loads(json.dumps(serialize_profile(make_profile()),
                                  ensure_ascii=False)))
        check('带上姓名与期望城市', '张三' in info and '西安' in info)
        check('带上技能', 'LangChain' in info, info[:200])
        check('带上项目亮点', '召回率' in info)
        check('带上薪资期望', '薪资期望' in info)
        # 这条是设计意图：规则分若喂给模型，模型的分就成了规则分的回声，40/60 混合失去意义
        for leak in ('match_score', 'matched_skills', 'rule_score', '90'):
            check('不含规则侧字段 %s' % leak, leak not in info, info[:300])

        try:
            DA.build_resume_info({})
            check('profile 全空要拒绝（否则拿到编造的分）', False, '没抛')
        except DA.LLMError:
            check('profile 全空要拒绝（否则拿到编造的分）', True)
        try:
            DA.build_resume_info({'basic_info': {}, 'skills': {}, 'projects': []})
            check('全是空值也算空', False, '没抛')
        except DA.LLMError:
            check('全是空值也算空', True)

        # ================================================================
        print('\n=== 2. build_job_requirements：没有 JD 时必须说明信息不足 ===')
        jobs = make_tier_jobs()
        cand_a = {'rank': 1, 'job': {k: v for k, v in (
            ('公司', jobs[0]['公司']), ('职位', jobs[0]['职位']), ('城市', jobs[0]['城市']),
            ('薪资', jobs[0]['salary']), ('经验', jobs[0]['experience']),
            ('技能标签', jobs[0]['skill_tags']),
            ('岗位要求和职责', jobs[0]['jd']), ('公司信息', jobs[0]['company_info']))}}
        block = DA.build_job_requirements(cand_a, jd_limit=100)
        check('带上公司与职位', '甲公司' in block and 'AI应用开发工程师' in block)
        check('带上技能标签', 'LangChain' in block)
        check('JD 按 jd_limit 截断', len(jobs[0]['jd']) > 100 and
              block.count('负责 RAG') <= 4, len(block))
        # 公司信息这一栏曾经整列为空（键名取错），这里钉一下它真的流到了模型
        check('公司信息进了提示词', '成立于 2019 年' in block, block[-300:])

        empty_jd = DA.build_job_requirements({'rank': 3, 'job': {'公司': '丙公司',
                                                                '技能标签': 'Python'}})
        check('没有 JD 时明确说信息不足', '未采集到 JD 全文' in empty_jd, empty_jd)
        check('并要求模型在 reason 里注明', 'reason' in empty_jd, empty_jd)

        # ================================================================
        print('\n=== 3. normalize_result：形状在这里收紧，别留到 merge ===')
        cand = {'rank': 7}
        rec = DA.normalize_result({'score': '85', 'category': 'QUALIFIED',
                                   'reason': ' 理由 ', 'missing_items': 'Django',
                                   'highlight': '亮点'}, cand)
        check('score 字符串转 int', rec['score'] == 85, rec)
        check('category 转小写', rec['category'] == 'qualified', rec)
        check('reason 去空白', rec['reason'] == '理由', rec)
        check('missing_items 字符串裹成列表', rec['missing_items'] == ['Django'], rec)
        check('rank 取自候选而非模型（模型给的 rank 不可信）',
              rec['rank'] == 7, rec)

        check('score 浮点四舍五入',
              DA.normalize_result({'score': 85.6, 'category': 'qualified'}, cand)['score'] == 86)
        check('score 超上界夹到 100',
              DA.normalize_result({'score': 150, 'category': 'qualified'}, cand)['score'] == 100)
        check('score 负数夹到 0',
              DA.normalize_result({'score': -5, 'category': 'qualified'}, cand)['score'] == 0)
        check('认 overall_match 别名',
              DA.normalize_result({'overall_match': 77, 'category': 'qualified'},
                                  cand)['score'] == 77)
        check('认 classification 别名',
              DA.normalize_result({'score': 1, 'classification': 'cannot_apply'},
                                  cand)['category'] == 'cannot_apply')
        check('四维细分打分留在文件里备查',
              'skills_match' in DA.normalize_result(
                  {'score': 80, 'category': 'qualified',
                   'skills_match': {'score': 8, 'note': 'ok'}}, cand))
        check('None 元素被过滤掉',
              DA.normalize_result({'score': 80, 'category': 'qualified',
                                   'missing_items': ['a', None, '  ', 'b']},
                                  cand)['missing_items'] == ['a', 'b'])

        for bad, why in (({'category': 'qualified'}, '缺 score'),
                         ({'score': '不是数字', 'category': 'qualified'}, 'score 不是数字'),
                         ({'score': 80}, '缺 category'),
                         ({'score': 80, 'category': ''}, 'category 是空串')):
            try:
                DA.normalize_result(bad, cand)
                check('拒绝残缺输出（%s）' % why, False, '没抛')
            except DA.LLMError:
                check('拒绝残缺输出（%s）' % why, True)
        try:
            DA.normalize_result(['不是对象'], cand)
            check('拒绝非对象输出', False, '没抛')
        except DA.LLMError:
            check('拒绝非对象输出', True)

        # ================================================================
        print('\n=== 4. 全流程：产出被真实 merge_deep_results 吃下 ===')
        run_dir = prepare(os.path.join(tmp, 'run_full'))
        seen = []
        code = run_main([run_dir, '--workers', '3'], make_stub(record=seen))
        check('全部成功时退出码 0', code == 0, code)
        check('三个岗位各发了一次请求', len(seen) == 3, seen and len(seen))
        check('每个岗位只问自己那份（公司名不重复）',
              sorted(c for c, _ in seen) == ['丙公司', '乙公司', '甲公司'],
              [c for c, _ in seen])

        results_path = deep_results_path(run_dir)
        data = read_json(results_path)
        check('deep_results.json 写出来了', os.path.exists(results_path))
        check('results 是 3 条', len(data['results']) == 3, data.get('results'))
        check('按 rank 升序', [r['rank'] for r in data['results']] == [1, 2, 3],
              [r['rank'] for r in data['results']])
        check('analyzed_by 标出模型（事后追责用）',
              'm-test' in data.get('analyzed_by', ''), data.get('analyzed_by'))
        by_rank = {r['rank']: r for r in data['results']}
        check('rank 1 拿到的是甲公司那份分（95）', by_rank[1]['score'] == 95, by_rank[1])
        check('rank 2 拿到的是乙公司那份分（40）', by_rank[2]['score'] == 40, by_rank[2])
        check('rank 3 拿到的是丙公司那份分（66）', by_rank[3]['score'] == 66, by_rank[3])
        check('highlight/risk 都在（HTML 报告要用）',
              by_rank[2]['risk'] == '经验年限硬门槛' and by_rank[1]['highlight'], by_rank[2])
        check('耗时进了 run_timings.jsonl',
              'deep_analyze' in open(timings_path(run_dir), encoding='utf-8').read())

        # ── 真实 merge ──
        classification = merge_deep_results(
            deep_candidates_path(run_dir), results_path,
            output_dir=run_dir)
        check('merge 返回了分类结果', classification is not None)
        all_jobs = (classification.qualified + classification.need_optimization
                    + classification.cannot_apply)
        check('三个岗位一个没丢', len(all_jobs) == 3, len(all_jobs))
        check('三个都标成深度分析过', all(j.get('is_deep') for j in all_jobs),
              [(j.get('公司'), j.get('is_deep')) for j in all_jobs])

        by_company = {j.get('company'): j for j in all_jobs}   # 岗位视图用 ASCII 键
        # 0.4 * (90/115*100) + 0.6 * 95 = 31.30 + 57.0 = 88.30 → 88
        check('甲公司混合分 = 88（规则 90 + 模型 95）',
              by_company['甲公司']['match_score'] == 88,
              by_company['甲公司']['match_score'])
        # 0.4 * (70/115*100) + 0.6 * 40 = 24.35 + 24.0 = 48.35 → 48
        check('乙公司混合分 = 48（规则 70 + 模型 40）',
              by_company['乙公司']['match_score'] == 48,
              by_company['乙公司']['match_score'])
        check('模型的分类盖住规则分类（乙公司规则说需优化、模型说不可投）',
              by_company['乙公司']['application_category'] == CATEGORY_CANNOT_APPLY,
              by_company['乙公司']['application_category'])
        check('模型的 reason 进了报告',
              '8 年经验' in by_company['乙公司'].get('application_category_reason', ''),
              by_company['乙公司'].get('application_category_reason'))
        check('highlight 传到了岗位视图',
              by_company['甲公司'].get('highlight') == 'RAG 经验直接对口',
              by_company['甲公司'].get('highlight'))
        check('HTML 报告生成了',
              os.path.exists(matching_report_path(run_dir)),
              os.listdir(run_dir))

        pool = read_json(qualified_jobs_path(run_dir))
        check('投递池有 2 个（符合 + 需优化）', len(pool) == 2, len(pool))
        check('模型判不可投的岗位不进投递池',
              all('乙公司' != j.get('公司') for j in pool), [j.get('公司') for j in pool])
        check('符合要求的排在前面', pool[0].get('公司') == '甲公司',
              [j.get('公司') for j in pool])
        check('投递池是原始爬取字段（link 在，供 CSV 回查）',
              all(j.get('link', '').startswith('http') for j in pool), pool[0].keys())
        stray = [k for j in pool for k in j
                 if k not in CSV_FIELDS and k != 'source_file']
        check('没有自造字段名（否则 write_application_md 回查不到）',
              not stray, stray)

        # ================================================================
        print('\n=== 5. 部分失败：不带崩全批，失败的沿用规则分类 ===')
        run_dir = prepare(os.path.join(tmp, 'run_partial'))
        code = run_main([run_dir], make_stub(fail_for=('丙公司',)))
        check('部分失败退出码 3（不是 0，也不是 1）', code == 3, code)
        data = read_json(deep_results_path(run_dir))
        check('成功的 2 条照样落盘', len(data['results']) == 2, data['results'])
        check('落盘的是甲乙两条', sorted(r['rank'] for r in data['results']) == [1, 2],
              [r['rank'] for r in data['results']])

        classification = merge_deep_results(
            deep_candidates_path(run_dir),
            deep_results_path(run_dir), output_dir=run_dir)
        all_jobs = (classification.qualified + classification.need_optimization
                    + classification.cannot_apply)
        check('失败的岗位没有消失', len(all_jobs) == 3, len(all_jobs))
        by_company = {j.get('company'): j for j in all_jobs}   # 岗位视图用 ASCII 键
        check('失败的那条标为未深度分析', by_company['丙公司'].get('is_deep') is False,
              by_company['丙公司'].get('is_deep'))
        # min(100, int(60/115*100)) = 52
        check('失败的那条用纯规则分 52', by_company['丙公司']['match_score'] == 52,
              by_company['丙公司']['match_score'])
        check('失败的那条沿用规则分类（需优化，仍可投）',
              by_company['丙公司']['application_category'] == CATEGORY_NEED_OPTIMIZATION,
              by_company['丙公司']['application_category'])
        check('它仍然在投递池里',
              any(j.get('公司') == '丙公司'
                  for j in read_json(qualified_jobs_path(run_dir))))

        # ================================================================
        print('\n=== 6. 全部失败：不写出半成品 ===')
        run_dir = prepare(os.path.join(tmp, 'run_allfail'))
        code = run_main([run_dir], make_stub(fail_for=('甲公司', '乙公司', '丙公司')))
        check('全失败退出码 1', code == 1, code)
        check('不写出空的 deep_results.json（免得 merge 拿它当结果）',
              not os.path.exists(deep_results_path(run_dir)))

        # ================================================================
        print('\n=== 7. --resume：跳过已有 rank，保留旧结果 ===')
        run_dir = prepare(os.path.join(tmp, 'run_resume'))
        run_main([run_dir], make_stub(fail_for=('丙公司',)))
        seen = []
        code = run_main([run_dir, '--resume'], make_stub(record=seen))
        check('续跑退出码 0', code == 0, code)
        check('只重跑了失败的那一个', [c for c, _ in seen] == ['丙公司'],
              [c for c, _ in seen])
        data = read_json(deep_results_path(run_dir))
        check('续跑后 3 条齐了', len(data['results']) == 3, len(data['results']))
        by_rank = {r['rank']: r for r in data['results']}
        check('旧的两条没被冲掉', by_rank[1]['score'] == 95 and by_rank[2]['score'] == 40,
              by_rank)
        check('新的那条补上了', by_rank[3]['score'] == 66, by_rank[3])

        seen = []
        code = run_main([run_dir, '--resume'], make_stub(record=seen))
        check('全都有结果时续跑不再请求', code == 0 and not seen, (code, seen))

        # ================================================================
        print('\n=== 8. --dry-run / --limit ===')
        run_dir = prepare(os.path.join(tmp, 'run_dry'))

        def boom(*a, **k):
            raise AssertionError('--dry-run 竟然发起了请求')

        code = run_main([run_dir, '--dry-run'], boom)
        check('--dry-run 一次请求都不发，退出码 0', code == 0, code)
        check('--dry-run 不写 deep_results.json',
              not os.path.exists(deep_results_path(run_dir)))

        run_dir = prepare(os.path.join(tmp, 'run_limit'))
        seen = []
        code = run_main([run_dir, '--limit', '2'], make_stub(record=seen))
        check('--limit 2 只分析 2 个', len(seen) == 2, [c for c, _ in seen])
        check('--limit 取的是前两名（rank 1、2）',
              sorted(r['rank'] for r in
                     read_json(deep_results_path(run_dir))['results'])
              == [1, 2])
        check('--limit 后退出码 0（没分析的不算失败）', code == 0, code)

        # ================================================================
        print('\n=== 9. 前置条件缺失时的报错 ===')
        empty = os.path.join(tmp, 'run_empty')
        os.makedirs(empty)
        check('没有 deep_candidates.json 退出码 1',
              run_main([empty], make_stub()) == 1)
        check('运行目录不存在退出码 1',
              run_main([os.path.join(tmp, '不存在的目录')], make_stub()) == 1)
        no_cand = os.path.join(tmp, 'run_nocand')
        os.makedirs(os.path.dirname(deep_candidates_path(no_cand)), exist_ok=True)
        with open(deep_candidates_path(no_cand), 'w', encoding='utf-8') as f:
            json.dump({'profile': {}, 'candidates': []}, f)
        check('候选列表为空退出码 1', run_main([no_cand], make_stub()) == 1)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ deep_analyze 测试全部通过（含真实 merge_deep_results 联测）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
