#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 deep_candidates.json 切成若干「自包含提示词分片」，供并行 subagent 分析。

为什么要分片（2026-08-13 复盘结论）：
原先 Phase 2 是主循环里的 Claude 自己读 deep_candidates.json，对 top N 个候选
逐一分析。两个后果：
  1. N 份 JD 全文 + N 份分析输出全堆在主上下文里 —— 那次实跑 preTokens
     冲到 167,609 触发第一次压缩，光压缩就花了 167 秒。
  2. 串行。15 个候选就是 15 次「读 JD → 想 → 输出」的顺序往返。

改成分片后：每个 subagent 只读一个 shard_NN.md（自包含：分析规则 + 简历摘要 +
本片 JD 全在里面，不必再读 deep_candidates.json、profile.json 或
prompts/match_analysis.st），产出写到 result_NN.json。主上下文只经手「派发」
和「合并」两步，JD 全文一次都不进主上下文。

注意分片文件是自包含的 —— 刻意用体积换主上下文：简历摘要在每片里各存一份。
摘要只有几百字，而 JD 全文才是大头，所以这笔交换是划算的。

用法:
  python scripts/shard_deep_candidates.py <run_dir> [--per-shard 4] [--max-jd 3000]
  python scripts/shard_deep_candidates.py            # 自动取最近的运行目录

产出:
  {run_dir}/deep_shards/shard_01.md   ← 派给 agent 的提示词
  {run_dir}/deep_shards/result_01.json ← agent 写回的结果（本脚本不创建）

之后:
  python scripts/check_artifacts.py <run_dir> --kinds deep_shards --wait 360
  python scripts/run_matcher.py --mode deep --merge --output-dir <run_dir>
  （merge 会自动把 result_*.json 收拢成 deep_results.json）
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stage_timer

SHARD_DIR = 'deep_shards'
DEFAULT_PER_SHARD = 4        # 4 个候选/片：单 agent 上下文小，15 个候选切 4 片
DEFAULT_MAX_JD = 3000        # 单个 JD 截断长度，防某些公司把整本手册贴进 JD

SKILL_CATEGORIES = ['programming', 'frameworks', 'tools', 'other']

# 分析规则：从 prompts/match_analysis.st 蒸出来的判定口径。
# 刻意内联而不是让 agent 去读模板文件 —— 少一次文件读取，且分片自包含。
# 改判定口径时这里和 prompts/match_analysis.st 要一起改。
RULES = """## 判定口径

对每个候选岗位给出 0-100 匹配度评分，评分维度：薪资重叠度、经验年限、学历硬门槛、技能重合度。
然后从三类中选一个投递建议分类：

- `qualified`：技能重合度高（命中 ≥4 项核心技能）、薪资在期望范围内、学历经验完全满足。
- `need_optimization`：核心技能缺 2-3 项但可短期补齐、薪资略有差距（≤3K）、或经验差 1 年内。
- `cannot_apply`：学历硬性不达标、经验差 ≥3 年、薪资期望差距过大（>8K）。

硬性规则（优先于上面的评分）：
- JD 要求硕士而简历是本科 → 直接 `cannot_apply`，其他维度满分也不例外。经验差 ≥3 年、薪资差 >8K 同理。
- 薪资写「面议」或无法解析 → 最高只能给 `need_optimization`，理由里注明「薪资面议待确认」。
  未经验证的薪资不该触发自动投递。
- `category` 只能是 `qualified` / `need_optimization` / `cannot_apply` 三个英文枚举值，不要输出中文标签。
"""


def build_resume_block(profile):
    """
    从 profile 拼一段紧凑的简历摘要。

    刻意不带 raw_text —— 那是整份简历原文，几千字，乘以分片数就是纯粹的浪费。
    技能分类与 report.py:55 保持一致。
    """
    skills = profile.get('skills') or {}
    all_skills = []
    for cat in SKILL_CATEGORIES:
        all_skills.extend(skills.get(cat) or [])

    edu = profile.get('education') or {}
    edu_parts = [edu.get('school', ''), edu.get('degree', ''), edu.get('major', '')]
    if edu.get('graduation_year'):
        edu_parts.append('%s届' % edu['graduation_year'])
    education_str = ' · '.join(p for p in edu_parts if p) or '未知'

    basic = profile.get('basic_info') or {}
    exp = profile.get('experience') or {}
    # `or` 而非 get 的第二参数：解析产出里这些值可能是 null 而非缺键（见
    # scoring.py:676 同一个坑），get 的默认值形同虚设
    total_years = exp.get('total_years') or 0
    salary = profile.get('salary_expectation') or {}
    sal_min = salary.get('min') or '?'
    sal_max = salary.get('max') or '?'

    lines = [
        '## 简历摘要',
        '',
        '- 姓名: %s' % (basic.get('name') or '未提取'),
        '- 目标岗位: %s' % (basic.get('target_position') or '未填'),
        '- 目标城市: %s' % (basic.get('city') or '未填'),
        '- 学历: %s' % education_str,
        '- 工作年限: %s' % ('%s年' % total_years if total_years else '应届/实习'),
        '- 期望薪资: %s-%sK' % (sal_min, sal_max),
        '- 技能: %s' % (', '.join(all_skills) if all_skills else '未提取'),
    ]

    projects = profile.get('projects') or []
    if projects:
        lines.append('- 项目经历:')
        for proj in projects[:5]:
            if isinstance(proj, dict):
                name = proj.get('name') or proj.get('title') or ''
                desc = proj.get('description') or proj.get('desc') or ''
                text = ('%s：%s' % (name, desc)).strip('：')
            else:
                text = str(proj)
            text = ' '.join(text.split())
            if len(text) > 200:
                text = text[:200] + '…'
            if text:
                lines.append('  - %s' % text)

    keywords = profile.get('keywords') or []
    if keywords:
        lines.append('- 关键词: %s' % ', '.join(str(k) for k in keywords[:20]))

    return '\n'.join(lines)


def build_candidate_block(candidate, max_jd):
    """渲染单个候选。rank 必须原样带上：merge 靠 rank 回填，不靠 job_id。"""
    job = candidate.get('job') or {}
    rank = candidate.get('rank')

    jd = (job.get('岗位要求和职责') or '').strip()
    truncated = False
    if len(jd) > max_jd:
        jd = jd[:max_jd]
        truncated = True
    if not jd:
        jd = '（该岗位未采集到 JD 正文，请仅凭下方结构化字段判断，并在 reason 里注明「JD 缺失」）'

    lines = [
        '### 候选 rank=%s' % rank,
        '',
        '- 职位: %s' % (job.get('职位') or ''),
        '- 公司: %s' % (job.get('公司') or ''),
        '- 城市/区域: %s %s' % (job.get('城市') or '', job.get('区域') or ''),
        '- 薪资: %s' % (job.get('薪资') or '未写'),
        '- 经验要求: %s' % (job.get('经验') or '未写'),
        '- 学历要求: %s' % (job.get('学历') or '未写'),
        '- 公司规模: %s' % (job.get('规模') or '未写'),
        '- 技能标签: %s' % (job.get('技能标签') or ''),
        '- 规则预评分: %s（仅供参考，你的判断优先）' % candidate.get('rule_score', 0),
        '',
        '岗位要求和职责%s:' % ('（已截断至 %d 字）' % max_jd if truncated else ''),
        '',
        '```',
        jd,
        '```',
    ]
    return '\n'.join(lines)


OUTPUT_CONTRACT = """## 输出要求

把结果写入文件（用 Write 工具，不要只在回复里输出）：

    {result_path}

文件内容必须是合法 JSON，结构如下 —— `results` 数组里**每个候选一条**，
本片共 {count} 个候选，rank 分别是 {ranks}，一个都不能漏：

```json
{{
  "results": [
    {{
      "rank": {first_rank},
      "score": 78,
      "category": "need_optimization",
      "reason": "分类的简短理由",
      "education_match": {{"score": 100, "match": true, "reason": "简短原因"}},
      "experience_match": {{"score": 85, "match": true, "reason": "简短原因"}},
      "skills_match": {{"score": 75, "matched": ["Python"], "missing": ["K8s"], "reason": "简短原因"}},
      "salary_match": {{"score": 90, "match": true, "reason": "简短原因"}},
      "missing_items": ["缺失项"],
      "optimization_points": ["可优化点"],
      "highlight": "这个候选最值得强调的一点",
      "risk": "投递风险提示"
    }}
  ]
}}
```

写完后，你的回复只需要一行：`done <文件路径> <条数>`。
不要把 JSON 内容复述到回复里 —— 结果已经在文件里了，复述一遍等于让主上下文
再付一次同样的 token（这正是本次改造要消掉的成本）。
"""


def build_shard_text(index, candidates, resume_block, result_path, max_jd):
    ranks = [c.get('rank') for c in candidates]
    parts = [
        '# 深度匹配分析 — 分片 %02d' % index,
        '',
        '你是资深的简历与职位匹配专家。本文件是自包含的：分析规则、简历摘要、'
        '待分析岗位全在下面，**不需要再读任何其他文件**。',
        '',
        '本片 %d 个候选，rank: %s' % (len(candidates), ', '.join(str(r) for r in ranks)),
        '',
        RULES,
        '',
        resume_block,
        '',
        '## 待分析岗位',
        '',
    ]
    for candidate in candidates:
        parts.append(build_candidate_block(candidate, max_jd))
        parts.append('')

    parts.append(OUTPUT_CONTRACT.format(
        result_path=result_path,
        count=len(candidates),
        ranks=', '.join(str(r) for r in ranks),
        first_rank=ranks[0] if ranks else 1,
    ))
    return '\n'.join(parts)


def shard(run_dir, per_shard, max_jd):
    candidates_file = os.path.join(run_dir, 'deep_candidates.json')
    if not os.path.exists(candidates_file):
        print('错误: 候选文件不存在 %s' % candidates_file)
        print('请先运行: python scripts/run_matcher.py --mode deep --profile <profile.json> --top N')
        return 1

    with open(candidates_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    candidates = data.get('candidates') or []
    if not candidates:
        print('错误: %s 里没有候选' % candidates_file)
        return 1

    resume_block = build_resume_block(data.get('profile') or {})

    shard_dir = os.path.join(run_dir, SHARD_DIR)
    os.makedirs(shard_dir, exist_ok=True)

    # 清掉上一轮的分片：残留的 shard/result 会让屏障和 merge 都算错数
    removed = 0
    for name in os.listdir(shard_dir):
        if name.startswith(('shard_', 'result_')):
            try:
                os.remove(os.path.join(shard_dir, name))
                removed += 1
            except OSError:
                pass
    if removed:
        print('清理上一轮残留分片: %d 个文件' % removed)

    groups = [candidates[i:i + per_shard] for i in range(0, len(candidates), per_shard)]

    written = []
    for index, group in enumerate(groups, 1):
        shard_path = os.path.join(shard_dir, 'shard_%02d.md' % index)
        result_path = os.path.join(shard_dir, 'result_%02d.json' % index)
        text = build_shard_text(index, group, resume_block,
                                result_path.replace('\\', '/'), max_jd)
        with open(shard_path, 'w', encoding='utf-8') as f:
            f.write(text)
        written.append((shard_path, result_path, group, len(text)))

    # ── 派发说明 ──
    print('=' * 60)
    print('  深度分析分片完成: %d 个候选 → %d 片' % (len(candidates), len(groups)))
    print('=' * 60)
    total_chars = sum(w[3] for w in written)
    print('分片目录: %s' % shard_dir)
    print('分片总字符: %d（平均 %d/片）' % (total_chars, total_chars // len(written)))
    print()
    print('下一步：并行派发 %d 个 subagent，每个的提示词就是下面这一句：' % len(groups))
    print()
    for shard_path, result_path, group, _ in written:
        ranks = ', '.join(str(c.get('rank')) for c in group)
        print('  Read %s 并按其中的输出要求执行（rank %s）'
              % (shard_path.replace('\\', '/'), ranks))
    print()
    print('全部派发后等产物齐全（不要靠通知判定，见 check_artifacts.py 文档）:')
    print('  python scripts/check_artifacts.py %s --kinds deep_shards --wait 360'
          % run_dir.replace('\\', '/'))
    print()
    print('齐全后合并（会自动收拢 result_*.json → deep_results.json）:')
    print('  python scripts/run_matcher.py --mode deep --merge --output-dir %s'
          % run_dir.replace('\\', '/'))
    print()

    stage_timer.mark(run_dir, 'deep_shard_dispatch',
                     note='%d candidates → %d shards' % (len(candidates), len(groups)))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='把 deep_candidates.json 切成自包含提示词分片，供并行 subagent 分析')
    ap.add_argument('run_dir', nargs='?', help='运行目录（省略则取最近一个）')
    ap.add_argument('--per-shard', type=int, default=DEFAULT_PER_SHARD,
                    help='每片候选数（默认 %d）' % DEFAULT_PER_SHARD)
    ap.add_argument('--max-jd', type=int, default=DEFAULT_MAX_JD,
                    help='单个 JD 截断长度（默认 %d）' % DEFAULT_MAX_JD)
    args = ap.parse_args()

    run_dir = args.run_dir
    if not run_dir:
        from resume_matcher.config import get_latest_run_dir
        run_dir = get_latest_run_dir()
        if not run_dir:
            print('错误: 找不到运行目录，请显式传入 run_dir')
            return 1
        print('自动选用最近的运行目录: %s' % run_dir)

    if args.per_shard < 1:
        print('错误: --per-shard 至少为 1')
        return 1

    return shard(run_dir, args.per_shard, args.max_jd)


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
