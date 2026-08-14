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
from .scoring import (
    compute_difficulty,
    build_job_view,
    CATEGORY_QUALIFIED,
    CATEGORY_NEED_OPTIMIZATION,
    CATEGORY_CANNOT_APPLY,
)
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
            # 规则侧的投递分类，作为 Claude 未覆盖该候选时的兜底
            'rule_application_category': job.get('application_category', CATEGORY_NEED_OPTIMIZATION),
            'rule_application_category_reason': job.get('application_category_reason', ''),
        })

    output = {
        'version': '1.0',
        'mode': 'deep',
        'profile': serialize_profile(profile),
        'candidates': candidates,
        'total': len(candidates),
        'prepared_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'instruction': (
            '不要在主上下文里逐个分析这些 candidates —— N 份 JD 全文进主上下文会触发压缩。'
            '改为 `python scripts/shard_deep_candidates.py <run_dir>` 切成自包含分片，'
            '每片派一个 subagent，结果写 deep_shards/result_NN.json，'
            f'再 `--merge` 收拢成 {output_dir}/deep_results.json。'
        ),
    }

    output_path = os.path.join(output_dir, 'deep_candidates.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n深度分析候选已保存: {output_path}")
    print(f"共 {len(candidates)} 个候选岗位待 Claude 深度分析")
    print(f"规则评分范围: {candidates[-1]['rule_score']} ~ {candidates[0]['rule_score']}")
    print(f"\n下一步: python scripts/shard_deep_candidates.py {output_dir}")
    print(f"        → 并行派发 subagent → check_artifacts.py --kinds deep_shards")
    print(f"然后运行: python run_matcher.py --mode deep --merge --run-id {os.path.basename(output_dir)}")

    return output_path


# ==================== Deep Results Merge ====================

SHARD_DIR = 'deep_shards'


def collect_shard_results(output_dir: str) -> Optional[str]:
    """
    把 deep_shards/result_*.json 收拢成一份 deep_results.json。

    并行分片方案的汇流点（见 scripts/shard_deep_candidates.py 的动机说明）：
    每个 subagent 只写自己那片的 result_NN.json，本函数按 rank 去重后合成
    merge_deep_results 期望的单文件格式。这样 merge 那侧完全不用改。

    没有分片目录 / 没有 result 文件时返回 None，调用方回落到「Claude 直接写了
    deep_results.json」的旧路径 —— 两种流程并存，不强制迁移。

    Returns:
        写出的 deep_results.json 路径，没有分片则 None
    """
    shard_dir = os.path.join(output_dir, SHARD_DIR)
    if not os.path.isdir(shard_dir):
        return None

    shard_files = sorted(
        name for name in os.listdir(shard_dir)
        if name.startswith('result_') and name.endswith('.json')
    )
    if not shard_files:
        return None

    merged: Dict[Any, Dict[str, Any]] = {}
    conflicts = []
    broken = []

    for name in shard_files:
        path = os.path.join(shard_dir, name)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (ValueError, OSError) as exc:
            # 单片坏掉不该让整批失败：记下来，其余照常合并
            broken.append((name, str(exc)))
            continue

        # 容忍两种写法：{"results": [...]} 和裸数组
        results = data.get('results', []) if isinstance(data, dict) else data
        if not isinstance(results, list):
            broken.append((name, 'results 不是数组'))
            continue

        for item in results:
            if not isinstance(item, dict):
                continue
            rank = item.get('rank')
            if rank is None:
                # 没有 rank 就无法回填到候选（merge 靠 rank 匹配），只能丢弃
                broken.append((name, '有一条结果缺 rank，已丢弃'))
                continue
            if rank in merged:
                conflicts.append(rank)
                continue          # 先到先得，不让后来的覆盖
            merged[rank] = item

    if not merged:
        print(f"警告: {shard_dir} 下的分片结果都无法使用")
        for name, why in broken:
            print(f"  ❌ {name}: {why}")
        return None

    results_list = [merged[k] for k in sorted(merged, key=lambda r: (r is None, r))]
    output = {
        'version': '1.0',
        'analyzed_by': 'claude-code-parallel-shards',
        'analyzed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'shard_files': shard_files,
        'results': results_list,
    }

    output_path = os.path.join(output_dir, 'deep_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n分片结果已收拢: {len(shard_files)} 片 → {len(results_list)} 条 → {output_path}")
    if conflicts:
        print(f"  ⚠ rank 重复（保留先出现的）: {sorted(set(conflicts))}")
    for name, why in broken:
        print(f"  ⚠ {name}: {why}")

    return output_path


# 深度分析权重：claude 评分占 60%，规则评分占 40%
DEEP_WEIGHT = 0.6
QUICK_WEIGHT = 0.4


def _normalize_category(value: str) -> Optional[str]:
    """
    Claude 输出的分类文本 → 标准 category 枚举。

    兼容三种写法：新枚举（qualified/need_optimization/cannot_apply）、
    旧提示词的中文标签（符合要求/可优化后投递/不可投递）、以及 direct_apply 别名。
    无法识别时返回 None，由调用方决定兜底。
    """
    if not value:
        return None
    c = value.strip().lower()

    if 'cannot' in c or '不可投递' in c or '不建议' in c:
        return CATEGORY_CANNOT_APPLY
    if 'need_optimization' in c or 'need optimization' in c or '可优化' in c or '优化后' in c:
        return CATEGORY_NEED_OPTIMIZATION
    if ('qualified' in c or 'direct_apply' in c or 'direct apply' in c
            or '符合要求' in c or '可直接投递' in c or '直接投递' in c):
        return CATEGORY_QUALIFIED
    return None


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
    # 原始岗位 dict（candidate['job']）随分类同步收集，供写出 qualified_jobs.json。
    # qualified_jobs.json 是 Stage 7 的默认投递候选池，用原始爬取字段（link/公司/职位/…），
    # 与 write_application_md.py 按 link 回查 CSV 的约定一致；不再由主代理手写。
    qualified_raw = []
    need_optimization_raw = []

    for candidate in candidates_data.get('candidates', []):
        job = candidate.get('job', {})
        rank = candidate.get('rank')
        rule_score = candidate.get('rule_score', 50)
        rule_analysis = candidate.get('rule_analysis', {})
        rule_category = candidate.get('rule_application_category', CATEGORY_NEED_OPTIMIZATION)

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
            deep_score = deep.get('score', deep.get('overall_match', deep.get('deep_score', rule_score)))
            # 规则分归一化到 0-100
            quick_normalized = min(100, rule_score / 115.0 * 100)
            blended = int(QUICK_WEIGHT * quick_normalized + DEEP_WEIGHT * deep_score)

            # 分类以 Claude 为准；无法识别时退回规则分类
            raw_category = deep.get('category', deep.get('classification', deep.get('deep_classification', '')))
            category = _normalize_category(raw_category) or rule_category

            # 原因：Claude 的 > 规则的
            reasons = deep.get(
                'reason',
                deep.get('classification_reason', '; '.join(rule_analysis.get('match_reasons', [])[:3])),
            )
            missing = deep.get('missing_items', rule_analysis.get('missing_skills', []))
            optimization = deep.get('optimization_points', rule_analysis.get('optimization_points', []))

            # 难度
            company_size = parse_company_size(job.get('公司', ''), job.get('规模', ''))
            difficulty = compute_difficulty(blended)

            # 亮点和风险（仅深度模式有）
            highlight = deep.get('highlight', '')
            risk = deep.get('risk', '')
        else:
            # ── 未深度分析的候选：沿用规则侧裁定的分类 ──
            blended = min(100, int(rule_score / 115.0 * 100))
            category = rule_category

            reasons = (candidate.get('rule_application_category_reason', '')
                       or '; '.join(rule_analysis.get('match_reasons', [])[:4]))
            missing = rule_analysis.get('missing_skills', [])
            optimization = rule_analysis.get('optimization_points', [])
            company_size = parse_company_size(job.get('公司', ''), job.get('规模', ''))
            difficulty = compute_difficulty(blended)
            highlight = ''
            risk = ''

        # ── 构建 job dict ──
        # 字段枚举统一在 scoring.build_job_view（见该函数注释）。深度模式只把自己
        # 算出来的那几项通过 overrides 盖上去，不再重建整个 dict。
        job_result = build_job_view(job, category, overrides={
            'job_id': job_id,
            'company_size': company_size,
            'match_score': blended,
            'difficulty': difficulty,
            'application_category': category,
            'application_category_reason': reasons,
            'classification_reason': reasons,
            'missing_items': missing[:5] if isinstance(missing, list) else [],
            'optimization_points': optimization if isinstance(optimization, list) else [],
            'highlight': highlight,
            'risk': risk,
            'is_deep': deep is not None,
        })

        if category == CATEGORY_QUALIFIED:
            qualified.append(job_result)
            qualified_raw.append(job)
        elif category == CATEGORY_CANNOT_APPLY:
            cannot_apply.append(job_result)
        else:
            need_optimization.append(job_result)
            need_optimization_raw.append(job)

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

    # ── 写出 qualified_jobs.json（Stage 7 默认投递候选池）──
    # 含「符合+需优化」两类，按置信度（符合在前）排序；7bc 询问投递范围后，
    # 主代理如需收窄，直接覆盖此文件即可。字段为原始爬取 dict，供
    # write_application_md.py 按 link 回查 CSV。
    qualified_path = os.path.join(output_dir, 'qualified_jobs.json')
    with open(qualified_path, 'w', encoding='utf-8') as f:
        json.dump(qualified_raw + need_optimization_raw, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 已生成投递候选池: {qualified_path}（{len(qualified_raw) + len(need_optimization_raw)} 个岗位，含符合+需优化）")

    return classification
