#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
深度分析辅助模块

提供深度模式的 Python 侧支持：
- save_deep_candidates: 保存预筛选候选供 Claude 读取
- merge_deep_results: 合并 Claude 深度分析与规则分数
- profile 序列化/反序列化
"""

import json
import os
import time
from typing import List, Dict, Any, Optional

from .config import ResumeProfile, JobClassification, OUTPUT_DIR
from .scoring import compute_difficulty
from .utils import parse_company_size, ensure_output_dir


# ==================== Profile 序列化 ====================

def serialize_profile(profile: ResumeProfile) -> Dict[str, Any]:
    """将 ResumeProfile 序列化为 JSON 兼容的 dict"""
    return {
        'basic_info': profile.basic_info,
        'education': profile.education,
        'experience': profile.experience,
        'skills': profile.skills,
        'projects': profile.projects,
        'awards': profile.awards,
        'publications': profile.publications,
        'social_links': profile.social_links,
        'salary_expectation': profile.salary_expectation,
        'keywords': profile.keywords,
        'raw_text': profile.raw_text,
    }


def deserialize_profile(data: Dict[str, Any]) -> ResumeProfile:
    """从 JSON dict 反序列化 ResumeProfile"""
    return ResumeProfile(
        basic_info=data.get('basic_info', {}),
        education=data.get('education', {}),
        experience=data.get('experience', {}),
        skills=data.get('skills', {}),
        projects=data.get('projects', []),
        awards=data.get('awards', []),
        publications=data.get('publications', []),
        social_links=data.get('social_links', {}),
        salary_expectation=data.get('salary_expectation', {}),
        keywords=data.get('keywords', []),
        raw_text=data.get('raw_text', ''),
    )


# ==================== Deep Candidates ====================

def save_deep_candidates(
    profile: ResumeProfile,
    tier1: List[Dict[str, Any]],
    tier2: List[Dict[str, Any]],
    top_n: int = 15,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """
    保存深度分析候选岗位列表

    从 tier1 + tier2 中取 match_score 最高的前 top_n 个岗位，
    保存完整数据（含 JD 文本、规则评分参考）供 Claude 深度分析。

    Args:
        profile: 简历解析结果
        tier1: 高匹配岗位列表（已含 match_score 等字段）
        tier2: 中匹配岗位列表
        top_n: 保留前 N 个候选
        output_dir: 输出目录

    Returns:
        JSON 文件路径
    """
    ensure_output_dir()

    # 合并 tier1 + tier2，按 match_score 降序
    merged = sorted(
        tier1 + tier2,
        key=lambda j: j.get('match_score', 0),
        reverse=True,
    )
    top_candidates = merged[:top_n]

    # 构建候选列表
    candidates = []
    for rank, job in enumerate(top_candidates, 1):
        candidates.append({
            'rank': rank,
            'rule_score': job.get('match_score', 0),
            'rule_difficulty': job.get('difficulty', 'Medium'),
            'job': {
                'link': job.get('link', ''),
                '职位': job.get('职位', ''),
                '公司': job.get('公司', ''),
                '城市': job.get('城市', ''),
                '区域': job.get('区域', ''),
                '商圈': job.get('商圈', ''),
                '薪资': job.get('salary', job.get('薪资', '')),
                '经验': job.get('experience', job.get('经验', '')),
                '学历': job.get('degree', job.get('学历', '')),
                '领域': job.get('领域', ''),
                '性质': job.get('性质', ''),
                '规模': job.get('规模', ''),
                '技能标签': job.get('skill_tags', job.get('技能标签', '')),
                '福利标签': job.get('welfare_tags', job.get('福利标签', '')),
                '岗位要求和职责': job.get('jd', job.get('岗位要求和职责', '')),
                '公司信息': job.get('company_info', job.get('公司信息', '')),
                '位置': job.get('位置', ''),
                'source_file': job.get('source_file', ''),
            },
            'rule_analysis': {
                'match_reasons': job.get('match_reasons', []),
                'matched_skills': job.get('matched_skills', []),
                'missing_skills': job.get('missing_skills', []),
                'optimization_points': job.get('optimization_points', []),
                'salary_score': job.get('salary_score', 0),
                'experience_score': job.get('experience_score', 0),
                'degree_score': job.get('degree_score', 0),
                'skills_score': job.get('skills_score', 0),
                'position_score': job.get('position_score', 0),
                'ai_bonus': job.get('ai_bonus', 0),
            },
        })

    output = {
        'version': '1.0',
        'mode': 'deep',
        'profile': serialize_profile(profile),
        'candidates': candidates,
        'total': len(candidates),
        'prepared_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'instruction': (
            '请 Claude Code 对以上 candidates 逐一进行深度匹配分析。'
            '对每个候选岗位，读取 prompts/match_analysis.st 模板，'
            '填入简历信息和岗位要求，输出结构化的匹配分析 JSON。'
            f'汇总所有结果写入 {output_dir}/deep_results.json'
        ),
    }

    output_path = os.path.join(output_dir, 'deep_candidates.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n深度分析候选已保存: {output_path}")
    print(f"共 {len(candidates)} 个候选岗位待 Claude 深度分析")
    print(f"规则评分范围: {candidates[-1]['rule_score']} ~ {candidates[0]['rule_score']}")
    print(f"\n下一步: Claude Code 读取 deep_candidates.json → 逐岗分析 → 写入 deep_results.json")
    print(f"然后运行: python run_matcher.py --mode deep --merge --run-id {os.path.basename(output_dir)}")

    return output_path


# ==================== Deep Results Merge ====================

# 深度分析权重：claude 评分占 60%，规则评分占 40%
DEEP_WEIGHT = 0.6
QUICK_WEIGHT = 0.4


def _map_chinese_classification(classification: str) -> str:
    """中文分类文本 → 标准 bucket 名"""
    c = classification.strip()
    if '符合要求' in c or '可直接投递' in c or 'qualified' in c.lower():
        return 'qualified'
    elif '可优化' in c or '优化后' in c or 'need_optimization' in c.lower():
        return 'need_optimization'
    else:
        return 'cannot_apply'


def merge_deep_results(
    candidates_file: str,
    deep_results_file: str,
    output_dir: str = OUTPUT_DIR,
) -> Optional[JobClassification]:
    """
    合并 Claude 深度分析结果与规则评分

    1. 读取 deep_candidates.json（含预筛选结果 + profile）
    2. 读取 deep_results.json（Claude 的逐岗深度分析）
    3. 对每个深度分析过的岗位：混合评分，更新分类
    4. 重新生成 JobClassification 和 HTML 报告

    Args:
        candidates_file: deep_candidates.json 路径
        deep_results_file: deep_results.json 路径
        output_dir: 输出目录

    Returns:
        合并后的 JobClassification，失败返回 None
    """
    # ── 加载数据 ──
    if not os.path.exists(candidates_file):
        print(f"错误: 候选文件不存在 {candidates_file}")
        return None
    if not os.path.exists(deep_results_file):
        print(f"错误: 深度分析结果文件不存在 {deep_results_file}")
        return None

    with open(candidates_file, 'r', encoding='utf-8') as f:
        candidates_data = json.load(f)
    with open(deep_results_file, 'r', encoding='utf-8') as f:
        deep_data = json.load(f)

    profile = deserialize_profile(candidates_data.get('profile', {}))
    deep_results = deep_data.get('results', [])

    if not deep_results:
        print("警告: deep_results.json 中没有分析结果")
        return None

    # 建立 rank → deep_result 映射（rank 比 job_id 更可靠：
    # 子代理输出只含 rank，不含 job_id）
    deep_map = {r.get('rank'): r for r in deep_results}

    # ── 重建分类桶 ──
    qualified = []
    need_optimization = []
    cannot_apply = []

    for candidate in candidates_data.get('candidates', []):
        job = candidate.get('job', {})
        rank = candidate.get('rank')
        rule_score = candidate.get('rule_score', 50)
        rule_analysis = candidate.get('rule_analysis', {})

        # 提取 job_id（用于输出标识，不影响匹配）
        import re
        link = job.get('link', '')
        job_id = ''
        if link:
            m = re.search(r'([A-Za-z0-9_-]{7,})(?:\.html|$)', link)
            if m:
                job_id = 'job_' + m.group(1)
        if not job_id:
            job_id = f"job_{abs(hash(link or str(id(job)))):x}"[:12]

        # 通过 rank 查找深度分析结果
        deep = deep_map.get(rank)

        if deep:
            # ── 混合评分 ──
            deep_score = deep.get('overall_match', deep.get('deep_score', rule_score))
            # 规则分归一化到 0-100
            quick_normalized = min(100, rule_score / 115.0 * 100)
            blended = int(QUICK_WEIGHT * quick_normalized + DEEP_WEIGHT * deep_score)

            # 分类以 Claude 为准
            classification = deep.get('classification', deep.get('deep_classification', ''))
            bucket = _map_chinese_classification(classification)

            # 原因：Claude 的 > 规则的
            reasons = deep.get('classification_reason', '; '.join(rule_analysis.get('match_reasons', [])[:3]))
            missing = deep.get('missing_items', rule_analysis.get('missing_skills', []))
            optimization = deep.get('optimization_points', rule_analysis.get('optimization_points', []))

            # 难度
            company_size = parse_company_size(job.get('公司', ''), job.get('规模', ''))
            difficulty = compute_difficulty(blended)

            # 亮点和风险（仅深度模式有）
            highlight = deep.get('highlight', '')
            risk = deep.get('risk', '')
        else:
            # ── 未深度分析的候选：用规则结果 ──
            blended = min(100, int(rule_score / 115.0 * 100))
            bucket = 'need_optimization'  # 默认
            if rule_score >= 100:
                bucket = 'qualified'
            elif rule_score < 85:
                bucket = 'cannot_apply'

            reasons = '; '.join(rule_analysis.get('match_reasons', [])[:4])
            missing = rule_analysis.get('missing_skills', [])
            optimization = rule_analysis.get('optimization_points', [])
            company_size = parse_company_size(job.get('公司', ''), job.get('规模', ''))
            difficulty = compute_difficulty(blended)
            highlight = ''
            risk = ''

        # ── 构建 job dict ──
        job_result = {
            'job_id': job_id,
            'link': job.get('link', ''),
            'company': job.get('公司', ''),
            'position': job.get('职位', ''),
            'city': job.get('城市', ''),
            'salary': job.get('薪资', ''),
            'experience': job.get('经验', ''),
            'education': job.get('学历', ''),
            'skill_tags': job.get('技能标签', ''),
            'welfare_tags': job.get('福利标签', ''),
            'company_info': (job.get('公司信息', '') or '')[:500],
            'scale': job.get('规模', ''),
            'company_size': company_size,
            'jd': (job.get('岗位要求和职责', '') or '')[:1000],
            'job_detail': (job.get('岗位要求和职责', '') or '')[:200],
            'source_file': job.get('source_file', ''),
            'match_score': blended,
            'difficulty': difficulty,
            'classification_reason': reasons,
            'missing_items': missing[:5] if isinstance(missing, list) else [],
            'optimization_points': optimization if isinstance(optimization, list) else [],
            'highlight': highlight,
            'risk': risk,
            'is_deep': deep is not None,
        }

        if bucket == 'qualified':
            qualified.append(job_result)
        elif bucket == 'cannot_apply':
            cannot_apply.append(job_result)
        else:
            need_optimization.append(job_result)

    # ── 按分数降序排序 ──
    for lst in [qualified, need_optimization, cannot_apply]:
        lst.sort(key=lambda j: j.get('match_score', 0), reverse=True)

    classification = JobClassification(
        qualified=qualified,
        need_optimization=need_optimization,
        cannot_apply=cannot_apply,
    )

    # ── 生成 HTML 报告 ──
    from .report import generate_html_report
    html_path = generate_html_report(
        profile, classification,
        output_dir=output_dir,
        analysis_mode='deep',
    )

    # ── 输出统计 ──
    deep_count = sum(1 for j in qualified + need_optimization + cannot_apply if j.get('is_deep'))
    print(f"\n深度分析合并完成:")
    print(f"  深度分析岗位: {deep_count}/{len(deep_results)}")
    print(f"  符合要求: {len(qualified)}")
    print(f"  需优化: {len(need_optimization)}")
    print(f"  不可投递: {len(cannot_apply)}")
    print(f"  HTML 报告: {html_path}")

    return classification
