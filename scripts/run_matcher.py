#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BOSS直聘岗位匹配系统 — 统一 CLI 入口

支持两种模式:
  - 快速模式 (quick): 纯规则 6 维度评分，秒级完成，零 token 消耗
  - 深度模式 (deep):  先规则预筛选，Top N 候选交 deep_analyze.py 送 LLM 逐岗分析

用法:
  # 快速模式（输出到 ./resume_output/<timestamp>/）
  python run_matcher.py --mode quick --profile ./resume_output/<timestamp>/profile.json

  # 深度模式 — 阶段 1: 准备候选
  python run_matcher.py --mode deep --profile ./resume_output/<timestamp>/profile.json --top 15

  # 深度模式 — 阶段 2: 调 LLM 逐岗分析
  python deep_analyze.py ./resume_output/<timestamp>

  # 深度模式 — 阶段 3: 合并分析结果（自动定位最近运行目录）
  python run_matcher.py --mode deep --merge

  # 深度模式 — 阶段 3: 指定运行目录
  python run_matcher.py --mode deep --merge --run-id 2026-08-09_14-30-00

  # 指定自定义输出目录（跳过时间戳子目录）
  python run_matcher.py --mode quick --profile profile.json --output-dir ./my_output
"""

import argparse
import json
import os
import sys

import stage_timer
from resume_matcher import (
    ResumeProfile,
    JobClassification,
    load_job_data,
    list_available_job_files,
    classify_jobs_advanced,
    tiers_to_classification,
    generate_html_report,
    generate_bauhaus_json,
    create_run_dir,
    get_latest_run_dir,
    OUTPUT_DIR,
)


def load_profile(profile_path: str) -> ResumeProfile:
    """从 JSON 文件加载简历 profile"""
    if not os.path.exists(profile_path):
        print(f"错误: profile 文件不存在: {profile_path}")
        print("请先解析简历: python scripts/parse_resume.py <简历文件>")
        sys.exit(1)

    with open(profile_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    profile = ResumeProfile(
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
    return profile


def run_quick_mode(profile: ResumeProfile, output_dir: str) -> None:
    """快速模式：纯规则 6 维度评分 → 分类 → 报告"""
    print("\n" + "=" * 60)
    print("  🚀 快速模式 — 纯规则 6 维度评分")
    print("=" * 60)

    # 1. 加载岗位数据
    job_files = list_available_job_files()
    if not job_files:
        print("错误: 未找到岗位数据文件（assets/post_data/ 目录下无 CSV）")
        # 只在这条错误路径上导入：boss_crawler 会连带加载 DrissionPage，而快速模式
        # 一个浏览器都不用，不该为一行提示付这个导入代价。
        from boss_crawler.utils import entry_cmd
        print("请先运行爬虫获取岗位数据: %s && %s"
              % (entry_cmd('--ensure-login'),
                 entry_cmd('-m', 'custom', '-p', '...', '-d', '-y')))
        sys.exit(1)

    print(f"\n找到 {len(job_files)} 个岗位数据文件:")
    for jf in job_files:
        print(f"  {jf['name']}: {jf['count']} 条")

    csv_paths = [jf['path'] for jf in job_files]
    jobs = load_job_data(csv_paths)
    print(f"\n共加载 {len(jobs)} 个岗位")

    # 2. 6 维度评分 + 分层
    tier1, tier2, tier3, tier4 = classify_jobs_advanced(profile, jobs)

    # 3. 转换为 JobClassification
    classification = tiers_to_classification(tier1, tier2, tier3)

    # 4. 生成报告
    html_path = generate_html_report(
        profile, classification,
        output_dir=output_dir,
        analysis_mode='quick',
    )
    json_path = generate_bauhaus_json(
        profile, tier1, tier2, tier3, tier4,
        output_dir=output_dir,
        analysis_mode='quick',
    )

    # 5. 保存全量评分结果
    scored_path = os.path.join(output_dir, 'scored_jobs.json')
    scored_output = {
        'tier1': tier1,
        'tier2': tier2,
        'tier3': tier3,
        'tier4': tier4,
        'total': len(jobs),
        'analysis_mode': 'quick',
    }
    with open(scored_path, 'w', encoding='utf-8') as f:
        json.dump(scored_output, f, ensure_ascii=False, indent=2)

    # 6. 总结
    print("\n" + "=" * 60)
    print("  📊 分析完成！")
    print("=" * 60)
    print(f"  Tier 1 (≥100 高匹配):    {len(tier1)} 个")
    print(f"  Tier 2 (≥85 中等匹配):   {len(tier2)} 个")
    print(f"  Tier 3 (≥70 低匹配):     {len(tier3)} 个")
    print(f"  Tier 4 (<70 不匹配):     {len(tier4)} 个")
    print(f"\n  分类结果:")
    print(f"  ✅ 符合要求:    {len(classification.qualified)} 个")
    print(f"  ⚡ 需优化:      {len(classification.need_optimization)} 个")
    print(f"  ❌ 不可投递:    {len(classification.cannot_apply)} 个")
    print(f"\n  输出文件:")
    print(f"  📄 HTML 报告:   {html_path}")
    print(f"  📋 JSON 分类:   {json_path}")
    print(f"  📊 全量评分:    {scored_path}")
    print()


def run_deep_prepare(profile: ResumeProfile, output_dir: str, top_n: int) -> None:
    """深度模式阶段 1：预筛选 + 保存候选"""
    from resume_matcher.deep_analysis import save_deep_candidates

    print("\n" + "=" * 60)
    print("  🧠 深度模式 — 阶段 1: 规则预筛选")
    print("=" * 60)

    # 1. 加载岗位数据
    job_files = list_available_job_files()
    if not job_files:
        print("错误: 未找到岗位数据文件")
        sys.exit(1)

    print(f"\n找到 {len(job_files)} 个岗位数据文件")
    csv_paths = [jf['path'] for jf in job_files]
    jobs = load_job_data(csv_paths)
    print(f"共加载 {len(jobs)} 个岗位")

    # 2. 快速预筛选
    tier1, tier2, tier3, tier4 = classify_jobs_advanced(profile, jobs)

    print(f"\n预筛选结果 (规则评分):")
    print(f"  Tier 1 (≥100): {len(tier1)} 个")
    print(f"  Tier 2 (≥85):  {len(tier2)} 个")
    print(f"  Tier 3 (≥70):  {len(tier3)} 个")
    print(f"  Tier 4 (<70):  {len(tier4)} 个")

    # 3. 保存候选
    candidate_path = save_deep_candidates(
        profile, tier1, tier2,
        top_n=top_n,
        output_dir=output_dir,
    )

    print(f"\n{'=' * 60}")
    print("  ⏳ 阶段 2: 深度分析（调 LLM）")
    print(f"{'=' * 60}")
    print(f"\n候选文件: {candidate_path}")
    print(f"\n  python scripts/deep_analyze.py {output_dir}")
    print(f"  python scripts/run_matcher.py --mode deep --merge --run-id {os.path.basename(output_dir)}")
    print(f"\n或一条命令跑完这两步: python scripts/pipeline.py --run-dir {output_dir} --from deep")


def run_deep_merge(output_dir: str) -> None:
    """深度模式阶段 3：合并分析结果 + 生成报告"""
    from resume_matcher.deep_analysis import merge_deep_results

    print("\n" + "=" * 60)
    print("  🧠 深度模式 — 阶段 3: 合并分析结果")
    print("=" * 60)

    candidates_file = os.path.join(output_dir, 'deep_candidates.json')
    results_file = os.path.join(output_dir, 'deep_results.json')

    if not os.path.exists(candidates_file):
        print(f"错误: 候选文件不存在 {candidates_file}")
        print("请先运行: python run_matcher.py --mode deep")
        sys.exit(1)

    if not os.path.exists(results_file):
        print(f"错误: 深度分析结果文件不存在 {results_file}")
        print(f"  先跑深度分析: python scripts/deep_analyze.py {output_dir}")
        sys.exit(1)

    classification = merge_deep_results(
        candidates_file=candidates_file,
        deep_results_file=results_file,
        output_dir=output_dir,
    )

    if classification:
        print(f"\n{'=' * 60}")
        print("  ✅ 深度模式分析完成！")
        print(f"{'=' * 60}")
        print(f"  📄 HTML 报告: {os.path.join(output_dir, 'matching_report.html')}")
        print()


def main():
    # Windows 控制台是 GBK，print 里任何 emoji（本文件就有 📄/🧠）都会抛
    # UnicodeEncodeError 并带崩整个匹配流程。放在 main() 头部，调用方就不必
    # 每条命令手加 PYTHONIOENCODING=utf-8。
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(
        description='BOSS直聘岗位匹配系统 — 简历与岗位智能匹配',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 快速模式（纯规则评分，自动创建时间戳子目录）
  python run_matcher.py --mode quick --profile ./resume_output/2026-08-09_14-30-00/profile.json

  # 深度模式阶段 1（预筛选 + 保存候选）
  python run_matcher.py --mode deep --profile ./resume_output/2026-08-09_14-30-00/profile.json --top 15

  # 深度模式阶段 2（合并 Claude 分析结果，自动定位最近运行目录）
  python run_matcher.py --mode deep --merge

  # 深度模式阶段 2（指定运行目录）
  python run_matcher.py --mode deep --merge --run-id 2026-08-09_14-30-00
        """
    )

    parser.add_argument(
        '--mode', '-m',
        choices=['quick', 'deep'],
        default='quick',
        help='匹配模式: quick=纯规则快速评分, deep=规则预筛 + LLM 深度分析 (默认: quick)',
    )
    parser.add_argument(
        '--profile', '-p',
        help='简历 profile JSON 文件路径（由 parse_resume.py 产出）',
    )
    parser.add_argument(
        '--top', '-n',
        type=int,
        default=15,
        help='深度模式保留前 N 个候选岗位 (默认: 15)',
    )
    parser.add_argument(
        '--merge',
        action='store_true',
        help='深度模式阶段 3: 把 deep_results.json 与规则评分合并、重算分类与报告',
    )
    parser.add_argument(
        '--output-dir', '-o',
        default=None,
        help='输出目录 (默认: ./resume_output/<timestamp>/ 自动创建时间戳子目录)',
    )
    parser.add_argument(
        '--run-id',
        default=None,
        help='指定复用已有的运行目录名（如 2026-08-09_14-30-00），'
             '用于 --merge 时定位之前的运行目录',
    )

    args = parser.parse_args()

    # ── 确定输出目录 ──
    if args.output_dir:
        # 用户显式指定：直接使用
        output_dir = args.output_dir
    elif args.run_id:
        # 复用已有运行目录
        output_dir = os.path.join(OUTPUT_DIR, args.run_id)
        if not os.path.isdir(output_dir):
            print(f"错误: 指定的运行目录不存在: {output_dir}")
            sys.exit(1)
        print(f"📂 复用运行目录: {output_dir}")
    elif args.mode == 'deep' and args.merge:
        # --merge 模式：尝试自动找到最近一次 deep 运行的目录
        latest = get_latest_run_dir()
        if latest and os.path.exists(os.path.join(latest, 'deep_candidates.json')):
            output_dir = latest
            print(f"📂 自动定位到最近运行目录: {output_dir}")
        else:
            # 回退：创建新目录（但 deep_candidates 不存在，后续 merge 会报错提示）
            output_dir = create_run_dir()
            print(f"📂 未找到之前的 deep 运行，创建新目录: {output_dir}")
    else:
        # 默认：创建时间戳子目录
        output_dir = create_run_dir()
        print(f"📂 输出目录: {output_dir}")

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    if args.mode == 'deep' and args.merge:
        # 深度模式 — 阶段 3: 合并
        with stage_timer.stage(output_dir, 'deep_merge'):
            run_deep_merge(output_dir)
        return

    # 快速模式 + 深度模式阶段 1 都需要 profile
    if not args.profile:
        parser.error("需要指定 --profile（简历 profile JSON 文件路径）")

    profile = load_profile(args.profile)

    if args.mode == 'quick':
        with stage_timer.stage(output_dir, 'quick_match'):
            run_quick_mode(profile, output_dir)
    elif args.mode == 'deep':
        with stage_timer.stage(output_dir, 'deep_prepare'):
            run_deep_prepare(profile, output_dir, args.top)


if __name__ == '__main__':
    main()
