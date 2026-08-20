#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""内置手工 fixture 数据集：一份简历 + N 条带 gold 的岗位样本。

数据来自代码内手工构造（离线、可断言），专门覆盖回归角点。这组数据与 PROFILE 是
密封对：主评分链路（evaluate_matcher --gold-fixtures）只用这一份 PROFILE，拒绝外部
自定义简历，保证 gold 标注（针对本 PROFILE 校准）与运行时简历始终一致。

对外只暴露 PROFILE / FIXTURES / build_fixtures 三个名字（经 fixtures/__init__ 再导出）。
"""

import os

# 一份精简但真实的简历 profile（各 fixture 内联引用同一份）。
# 技能类目对齐 classify_jobs_advanced 的 [programming/frameworks/tools/other]。
PROFILE = {
    'basic_info': {'name': '张三', 'status': '离职-随时到岗', 'city': '北京',
                   'availability': {'can_start': '随时', 'days_per_week': '5天'}},
    'education': {'school': '某大学', 'degree': '本科', 'major': '计算机'},
    'experience': {'total_years': 3,
                   'companies': [{'name': '某科技', 'position': '后端', 'duration': '2023-2025',
                                  'highlights': ['订单查询 P99 从 800ms 降到 120ms']}]},
    'skills': {'programming': ['Python'], 'frameworks': ['FastAPI', 'Django'],
               'tools': ['Redis', 'MySQL', 'k8s', 'es'], 'other': ['RAG']},
    'projects': [{'name': '智能问答系统', 'tech_stack': 'Python/FAISS',
                  'highlights': ['召回率升 30%']}],
    'keywords': ['Python', 'FastAPI', 'RAG', '后端开发', 'AI'],
    'salary_expectation': {'min': 25, 'max': 35},
    'awards': [], 'publications': [],
}

_JOB_FIELDS = ('link', '公司', '职位', '薪资', '经验', '学历', '技能标签', '岗位要求和职责')


def _sample(link, company, position, salary, exp, degree, tags, jd, gold):
    """把一条 fixture 拼成样本 dict（job 走 _JOB_FIELDS 白名单 + gold + 内联简历）。"""
    job = {'link': link, '公司': company, '职位': position, '薪资': salary, '经验': exp,
           '学历': degree, '技能标签': tags, '岗位要求和职责': jd}
    gold = dict(gold)
    gold.setdefault('source', 'hand')
    return {'link': link, 'job': job, 'gold': gold, 'profile': dict(PROFILE)}


# 覆盖角点：
#   A1-A3 三档边界：技能命中4 / 经验差3（硬门槛）/ 薪资差8K（硬门槛 > 8 才是硬性）。
#   A4 面议：薪资不可解析 → 不得判 qualified（与 scoring 语义一致）。
#   A5 别名：k8s⇄kubernetes、es⇄elasticsearch —— 命中要靠别名归一。
#   A6 学历硬门槛不达标（JD 硕士 vs 简历本科）。
#   A7 空技能/缺 JD：只能依赖 reason 与 missing 推导，不得崩。
#   A8 简历技能完全没进 JD（漏投倾向：gold=need_optimization，规则若凭别名误判 qualified 才是错）。
FIXTURES = [
    # A1 qualified：技能 4 项命中 + 薪资匹配 + 学历经验满足
    _sample(
        'https://jobs.eval.local/match/1', '借呗科技', 'Python后端工程师', '25-35K', '1-3年', '本科',
        'Python,FastAPI,Redis,MySQL,Django',
        '负责 Python 后端与 FastAPI 服务开发，用 Redis 做缓存、MySQL 做存储。',
        {'category': 'qualified', 'score': 82,
         'matched_skills': ['Python', 'FastAPI', 'Redis', 'MySQL'],
         'missing_skills': [], 'reason': '核心技能基本命中，薪资在期望区间，学历经验满足'}),
    # A2 need_optimization：经验差 2 年（<3 未触发硬门槛），技能差 1 项
    _sample(
        'https://jobs.eval.local/match/2', '元象网络', '资深Python开发', '20-30K', '3-5年', '本科',
        'Python,Django,Redis',
        '要求 3-5 年 Python 开发经验，负责核心业务服务。',
        {'category': 'need_optimization', 'score': 66,
         'matched_skills': ['Python', 'Django', 'Redis'],
         'missing_skills': ['高并发架构'],
         'reason': '技能命中但经验差 2 年，优化简历后可投'}),
    # A3 cannot_apply：薪资差 >8K（10-15K vs 期望 25-35），无硬学历/经验问题
    _sample(
        'https://jobs.eval.local/match/3', '小微创业', 'Python开发', '10-15K', '1-3年', '本科',
        'Python,Flask',
        '负责内部工具开发，Flask 为主。',
        {'category': 'cannot_apply', 'score': 38,
         'matched_skills': ['Python'], 'missing_skills': ['Flask'],
         'reason': '薪资差距过大（>8K），命中硬门槛'}),
    # A4 面议：薪资不可解析 → 不得判 qualified（薪资未被验证）
    _sample(
        'https://jobs.eval.local/match/4', '神秘公司', 'AI工程师', '面议', '3-5年', '本科',
        'Python,RAG,LLM',
        '负责 Agent 与大模型应用开发，薪资面议。',
        {'category': 'need_optimization', 'score': 60,
         'matched_skills': ['Python', 'RAG', 'LLM'],
         'missing_skills': [],
         'reason': '技能契合但薪资面议无法验证，需确认后投'}),
    # A5 别名：k8s ⇄ Kubernetes、es ⇄ Elasticsearch，命中靠别名归一
    _sample(
        'https://jobs.eval.local/match/5', '云算科技', '平台工程师', '26-36K', '2-4年', '本科',
        'Kubernetes,Elasticsearch,Go',
        '用 k8s 部署微服务，用 ES 做检索平台。',
        {'category': 'need_optimization', 'score': 72,
         'matched_skills': ['Kubernetes', 'Elasticsearch'],
         'missing_skills': ['Go'],
         'reason': 'k8s/es 别名命中但仅 2 项达标技能，缺 Golang'}),
    # A5'：简历写 k8s/es（别名形），JD 用 Kubernetes/Elasticsearch（规范形）。命中靠
    # _normalize_skill 折叠；但命中仅 2 项（< 达标 4）→ should be need_optimization，不是 qualified。
    # A6 学历硬门槛：JD 硕士，简历本科 → cannot_apply
    _sample(
        'https://jobs.eval.local/match/6', '前沿研究院', '算法工程师', '30-40K', '3-5年', '硕士',
        'Python,机器学习,大模型',
        '深入 ML 与大模型，要求硕士及以上学历。',
        {'category': 'cannot_apply', 'score': 42,
         'matched_skills': ['Python'], 'missing_skills': ['机器学习', '大模型'],
         'reason': '学历硬性不达标（JD 硕士 vs 简历本科）'}),
    # A7 缺 JD 全文/技能标签空：不崩，missing 走 reason 与规则兜底
    _sample(
        'https://jobs.eval.local/match/7', '未知岗', '开发岗', '25-35K', '1-3年', '本科',
        '', '',
        {'category': 'need_optimization', 'score': 55,
         'matched_skills': [], 'missing_skills': [],
         'reason': 'JD 信息不足，无法确认技能与要求'}),
    # A8 简历技能没进 JD：gold=need_optimization（不该凭猜 qualified）
    _sample(
        'https://jobs.eval.local/match/8', '异域厂', '硬件工程师', '25-35K', '3-5年', '本科',
        '嵌入式,C语言,FPGA',
        '负责嵌入式与 FPGA 开发。',
        {'category': 'need_optimization', 'score': 48,
         'matched_skills': [], 'missing_skills': ['嵌入式', 'C语言', 'FPGA'],
         'reason': '技术栈完全不在简历内，但无硬门槛，方向跨得太远'}),
]


def build_fixtures():
    """返回新鲜样本 dict 列表（每次 deep copy，防调用方改坏数据集）。"""
    return [dict(f) for f in FIXTURES]


if __name__ == '__main__':
    _here = os.path.dirname(os.path.abspath(__file__))
    print('fixture 内置简历: %s skills + %d 条样本' % (len(PROFILE['skills']), len(FIXTURES)))