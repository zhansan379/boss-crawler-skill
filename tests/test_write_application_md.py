# -*- coding: utf-8 -*-
"""write_application_md.py 的回归测试：匹配分析必须能在 投递.md 里看到。

历史 bug：投递.md 的「匹配分析」四列（匹配度/投递难度/投递结论/结论理由）全是
「未裁定」，下方的 匹配理由/命中技能/亮点/风险/优化建议 小节整块缺失。根因是
write_application_md 只读 qualified_jobs.json —— 那堆文件按设计只含原始爬取字段，
不含任何匹配结论；裁定早在 merge_deep_results 的 job_result 里算完就丢了。

修复：merge 按 link 把裁定结论落盘到 state/match_analysis.json，write_one 写投递.md 前
按 link 连回 job dict。这里用真实 write_one + 手工构造的 match_analysis.json 验证：
连上了就显示真实值，没连上（快速模式/没 merge）就回退「未裁定」且不崩。

跑法：python tests/test_write_application_md.py
"""

import os
import sys
import json
import shutil
import tempfile
import argparse

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, 'scripts'))
sys.path.insert(0, os.path.join(_REPO, 'scripts', 'deliver'))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import write_application_md as WAM
from resume_matcher.paths import qualified_jobs_path, match_analysis_path

LINK_A = 'https://www.zhipin.com/job_detail/aaaaaaa1.html'
LINK_B = 'https://www.zhipin.com/job_detail/bbbbbbb2.html'

JOB_A = {
    'link': LINK_A, '公司': '甲公司', '职位': 'AI应用开发工程师', '城市': '西安',
    '薪资': '15-25K', '经验': '1-3年', '学历': '本科', '技能标签': 'Python RAG',
    '福利标签': '五险一金', 'source_file': '',
}

# match_analysis.json 里某岗位的裁定记录：键名 = render() 里 job.get(...) 读的那几个
MATCH_A = {
    'match_score': 88,
    'difficulty': '较低',
    'application_category': 'qualified',
    'application_category_reason': '技能与 JD 高度契合',
    'match_reasons': ['技能高度匹配', '语言对口'],
    'matched_skills': ['Python', 'FastAPI'],
    'missing_items': [],
    'optimization_points': ['突出 RAG 项目'],
    'highlight': 'RAG 经验直接对口',
    'risk': '',
}


def prepare(run_dir):
    os.makedirs(os.path.dirname(qualified_jobs_path(run_dir)), exist_ok=True)
    # 只有 A 有裁定记录；B 故意不写，验证缺数源时回退「未裁定」
    with open(qualified_jobs_path(run_dir), 'w', encoding='utf-8') as f:
        json.dump([JOB_A, {'link': LINK_B, '公司': '乙公司', '职位': '架构师',
                          '城市': '北京', '薪资': '40-60K', '技能标签': 'Java'}],
                  f, ensure_ascii=False)
    with open(match_analysis_path(run_dir), 'w', encoding='utf-8') as f:
        json.dump({LINK_A: MATCH_A}, f, ensure_ascii=False)
    os.makedirs(os.path.join(run_dir, 'materials'), exist_ok=True)
    with open(os.path.join(run_dir, 'materials', 'greeting_1_甲公司.txt'),
              'w', encoding='utf-8') as f:
        f.write('您好，我是张三。')
    return run_dir


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:400]))
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp(prefix='wam_test_')
    try:
        run_dir = prepare(tmp)
        args = argparse.Namespace(greeting='', greeting_file=None, csv=None)

        # ── A：按 link 连回了 match_analysis，匹配分析是真实值 ──
        out_a, missing_a = WAM.write_one(run_dir, JOB_A, 1, args)
        check('写出了 A 的 投递.md（在 #N-公司-岗位 目录）', os.path.exists(out_a), out_a)
        text = open(out_a, encoding='utf-8').read()

        check('回查原始 CSV 失败仍写出文件（csv_path 空，字段不全但文档在）',
              os.path.exists(out_a), missing_a)
        check('没找到 CSV 时如实标注数据来源（不改行为，只如实说明）',
              '未找到原始 CSV' in text, text[:200])
        check('匹配度显示混合分 88%', '88%' in text, text)
        check('投递难度显示出来（不再未裁定）', '较低' in text, text)
        check('投递结论显示 qualified', 'qualified' in text, text)
        check('结论理由来自深度分析 reason',
              '技能与 JD 高度契合' in text, text)
        check('「匹配分析」四列一个都不再未裁定', '未裁定' not in text,
              [ln for ln in text.splitlines() if '匹配' in ln or '未裁定' in ln])
        for snippet in ('技能高度匹配', 'FastAPI', '突出 RAG 项目', 'RAG 经验直接对口'):
            check('小节内容「%s」落进了文件' % snippet, snippet in text)
        check('命中技能逐条渲染成子弹列表',
              '- Python' in text and '- FastAPI' in text,
              [ln for ln in text.splitlines() if 'Python' in ln or 'FastAPI' in ln])

        # ── B：match_analysis.json 里没有它 → 回退「未裁定」，但不崩 ──
        out_b, missing_b = WAM.write_one(run_dir, JOB_B(), 2, args)
        text_b = open(out_b, encoding='utf-8').read()
        check('没连上时 B 照样写出来（缺数源不崩）', os.path.exists(out_b), out_b)
        check('B 的匹配度回退未裁定', '未裁定' in text_b,
              [ln for ln in text_b.splitlines() if '匹配' in ln])

        # ── match_analysis.json 整个不存在（快速模式）→ 仍能写，不报错 ──
        os.remove(match_analysis_path(run_dir))
        check('删掉 match_analysis.json 后仍可读出空 dict',
              WAM.load_match_analysis(run_dir) == {})

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ write_application_md 测试全部通过（匹配分析已能落进 投递.md）')
    return 0


def JOB_B():
    return {'link': LINK_B, '公司': '乙公司', '职位': '架构师', '城市': '北京',
            '薪资': '40-60K', '技能标签': 'Java', 'source_file': ''}


if __name__ == '__main__':
    sys.exit(main())