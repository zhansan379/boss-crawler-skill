# -*- coding: utf-8 -*-
"""技术栈锁死 + 主次技能加权 + AI 门控的评分测试。

场景：候选核心语言 Java，agent/python 只是次要技能。验证：
  1. 「Python 后端开发」岗（非核心语言、且不涉 AI）→ 技术栈锁死：category=cannot_apply、分数封顶 ≤75、
     reason 含「技术栈不匹配」——从 qualified 级沉底，auto-apply 永不触碰。
  2. 「Python·RAG/Agent」岗（JD 有 AI 词 + 候选具备 AI 能力）→ 豁免锁死，不被误判不可投。
  3. 「Java 后端」岗 → 核心语言命中，不锁死，得分正常。
  4. 兼容：core_languages=None（旧调用等价）→ 锁死静默关闭，行为不变。
  5. AI 门控：候选无 AI 能力时，即使 JD 堆 AI 词也 ai_bonus=0，且仍被锁死。
  6. 主次技能加权：给 skill_weights 时 tools 类打折，不给时维持 5 分/项。

跑法：python tests/test_scoring_stack_lock.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

from resume_matcher.scoring import (  # noqa: E402
    score_job_advanced,
    CATEGORY_CANNOT_APPLY, CATEGORY_NEED_OPTIMIZATION, CATEGORY_QUALIFIED,
    _core_languages, _candidate_has_ai, SKILL_CATEGORY_WEIGHTS,
)

# —— Java 核心简历（project 全是 Java，agent/python 次要）——
JAVA_SKILLS = ['Java', 'Spring', 'SpringBoot', 'SpringCloud', 'MySQL', 'Redis', 'Docker', 'RAG']
JAVA_CORE = {'java'}
JAVA_WEIGHTS = {'java': 1.0, 'spring': 1.0, 'springboot': 1.0, 'springcloud': 1.0,
                'mysql': 0.6, 'redis': 0.6, 'docker': 0.6, 'rag': 0.35}

BASE = dict(
    salary_min=25, salary_max=35,
    user_experience_years=3, user_degree='本科',
    resume_keywords=['后端', 'AI', 'Java'],
)

_REQ = {'学历要求': '本科', '经验要求': '1-3年', '薪资范围': '25-35K'}


def py_backend():
    """普通 Python 后端岗：要求 Python，JD 无 AI 词。"""
    return {'link': 'l1', '职位': 'Python后端开发', '薪资': '25-35K', '经验': '1-3年', '学历': '本科',
            '技能标签': 'Python,Flask,MySQL,Redis',
            '岗位要求和职责': '负责 Python 后端与 FastAPI 服务开发，用 Redis 做缓存、MySQL 做存储。',
            '_jd_req': dict(_REQ, 技能要求=['Python', 'Flask'])}


def py_ai():
    """Python·RAG/Agent 岗：JD 一堆 AI 词。"""
    return {'link': 'l2', '职位': 'Python·RAG工程师', '薪资': '25-35K', '经验': '1-3年', '学历': '本科',
            '技能标签': 'Python,RAG,Agent,LLM,MySQL,Redis,Docker',
            '岗位要求和职责': '负责 Agent 与大模型应用开发，RAG 多路召回、LangChain 工作流。',
            '_jd_req': dict(_REQ, 技能要求=['Python', 'RAG'])}


def java_backend():
    """Java 后端岗：与核心语言一致。"""
    return {'link': 'l3', '职位': 'Java后端开发', '薪资': '25-35K', '经验': '1-3年', '学历': '本科',
            '技能标签': 'Java,Spring,SpringBoot,MySQL,Redis,Docker',
            '岗位要求和职责': '负责 Java 微服务与 Spring Boot 后端开发。',
            '_jd_req': dict(_REQ, 技能要求=['Java', 'SpringBoot'])}


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:300]))
        if not cond:
            failures.append(label)

    # ================================================================
    print('=== 1. Java 核心 vs Python 后端（无 AI）→ 技术栈锁死 ===')
    r = score_job_advanced(py_backend(), resume_skills=JAVA_SKILLS, core_languages=JAVA_CORE,
                           has_ai_capability=True, skill_weights=JAVA_WEIGHTS, **BASE)
    check('压成不可投', r['application_category'] == CATEGORY_CANNOT_APPLY,
          (r['application_category'], r['application_category_reason']))
    check('分数封顶 ≤75', r['match_score'] <= 75, r['match_score'])
    check('理由含技术栈不匹配', '技术栈不匹配' in r['application_category_reason'],
          r['application_category_reason'])
    check('难度 Hard', r['difficulty'] == 'Hard', r['difficulty'])

    # ================================================================
    print('\n=== 2. Java 核心 vs Python·RAG/Agent（有 AI）→ 豁免锁死 ===')
    r2 = score_job_advanced(py_ai(), resume_skills=JAVA_SKILLS, core_languages=JAVA_CORE,
                            has_ai_capability=True, skill_weights=JAVA_WEIGHTS, **BASE)
    check('不被误判不可投', r2['application_category'] != CATEGORY_CANNOT_APPLY,
          (r2['application_category'], r2['application_category_reason']))
    check('理由不含技术栈不匹配', '技术栈不匹配' not in r2['application_category_reason'],
          r2['application_category_reason'])
    check('因候选具备 AI 拿到 AI 加分', r2['ai_bonus'] > 0, r2['ai_bonus'])

    # ================================================================
    print('\n=== 3. Java 核心 vs Java 后端 → 不锁死、得分正常 ===')
    r3 = score_job_advanced(java_backend(), resume_skills=JAVA_SKILLS, core_languages=JAVA_CORE,
                            has_ai_capability=True, skill_weights=JAVA_WEIGHTS, **BASE)
    check('不锁死', r3['application_category'] != CATEGORY_CANNOT_APPLY,
          (r3['application_category'], r3['application_category_reason']))
    check('技能分 > 0', r3['skills_score'] > 0, r3['skills_score'])
    check('总分明显高于被锁死的 Python 岗', r3['match_score'] > r['match_score'],
          (r3['match_score'], r['match_score']))

    # ================================================================
    print('\n=== 4. 兼容：core_languages=None（旧调用等价）→ 锁死静默关闭 ===')
    r4 = score_job_advanced(py_backend(), resume_skills=JAVA_SKILLS, core_languages=None,
                            has_ai_capability=True, **BASE)
    check('不触发技术栈锁死', '技术栈不匹配' not in r4.get('match_reasons', []),
          r4.get('match_reasons'))

    # ================================================================
    print('\n=== 5. AI 门控：候选无 AI 能力 → ai_bonus=0 且仍被锁死 ===')
    r5 = score_job_advanced(py_ai(), resume_skills=['Java', 'Spring', 'MySQL'],
                            core_languages=JAVA_CORE, has_ai_capability=False, **BASE)
    check('ai_bonus=0（JD 堆 AI 词也不白拿）', r5['ai_bonus'] == 0, r5['ai_bonus'])
    check('仍被技术栈锁死', r5['application_category'] == CATEGORY_CANNOT_APPLY and
          '技术栈不匹配' in r5['application_category_reason'],
          (r5['application_category'], r5['application_category_reason']))

    # ================================================================
    print('\n=== 6. 主次技能加权：tools 类打折，缺省维持 5 分/项 ===')
    wx_job = {'职位': '通用岗', '薪资': '25-35K', '经验': '1-3年', '学历': '本科',
              '技能标签': 'MySQL,Redis', '岗位要求和职责': '用 MySQL 与 Redis 做存储缓存。'}
    w_skills = ['MySQL', 'Redis']
    with_w = score_job_advanced(wx_job, resume_skills=w_skills,
                                core_languages=JAVA_CORE, has_ai_capability=True,
                                skill_weights={'mysql': 0.6, 'redis': 0.6, 'java': 1.0}, **BASE)
    wo_w = score_job_advanced(wx_job, resume_skills=w_skills, core_languages=JAVA_CORE,
                              has_ai_capability=True, **BASE)
    check('给权重：tools 项 0.6×2 → skills_score=1', with_w['skills_score'] == 1,
          with_w['skills_score'])
    check('不给权重：2 项×5 → skills_score=10', wo_w['skills_score'] == 10,
          wo_w['skills_score'])
    check('加权不影响类别判定（仍按 len(matched)）', with_w['application_category'] == wo_w['application_category'],
          (with_w['application_category'], wo_w['application_category']))

    # ================================================================
    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ 技术栈锁死测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())