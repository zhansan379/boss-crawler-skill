# Job Matching Reference

## Mode Selection

Ask user to choose via `AskUserQuestion`:

| Scenario | Recommended mode | Reason |
|----------|-----------------|--------|
| 100+ jobs | Quick | Deep mode token cost too high |
| < 50 jobs | Deep | Precision gain worth the tokens |
| User says "fast" | Quick | Respect user preference |
| User says "detailed" | Deep | Respect user preference |
| Unclear | Ask user | Let user decide |

---

## Quick Mode

Single command, rule-based 6-dimension scoring (0-115 points), zero token cost.

```bash
python scripts/run_matcher.py --mode quick --profile {run_dir}/profile.json --output-dir {run_dir}
```

Script auto-completes: load CSVs → 6-dimension scoring → 4-tier classification → HTML report.

HTML report shows `🚀 快速模式` badge. Matching reasons are rule-based ("技能命中:7项", "薪资匹配"). No "view optimization suggestions" button.

---

## Deep Mode

Three phases. For jobs where you want semantic matching and per-job personalized optimization advice.

### Phase 0: Ask User for Top-N

Use `AskUserQuestion`:

> Deep mode sorts jobs by rule score, then sends the top N to Claude for per-job LLM analysis. Choose pre-filter count:
> - Top 5 — fastest, lowest token cost
> - Top 10 — balanced (recommended default)
> - Top 15 — broader coverage
> - Top 20 — maximum coverage, highest token cost
> - Custom — enter any number

### Phase 1: Python Pre-Filter

```bash
python scripts/run_matcher.py --mode deep --profile {run_dir}/profile.json --top <N> --output-dir {run_dir}
```

Saves `deep_candidates.json` to `{run_dir}/`. Contains top N candidates (from Tier1+Tier2) with full profile and rule scores.

### Phase 2: Claude Deep Analysis

Claude reads `{run_dir}/deep_candidates.json`, then for each candidate (in rank order):

1. Read the job's `岗位要求和职责` field (JD text)
2. Read `scripts/prompts/match_analysis.st` template
3. Fill resume info and job requirements into the template
4. Output structured JSON per job:

```json
{
  "job_id": "job_xxxxx",
  "overall_match": 85,
  "classification": "符合要求",
  "classification_reason": "核心技能高度匹配，RAG/Agent经验是亮点",
  "education_match": {"score": 90, "match": true, "reason": "本科学历满足要求"},
  "experience_match": {"score": 80, "match": true, "reason": "2年与要求的3年接近"},
  "skills_match": {"score": 85, "matched": ["Python", "LangChain", "RAG"], "missing": ["K8s运维"], "reason": "核心AI技能匹配，缺少运维经验"},
  "missing_items": ["Kubernetes运维经验", "高并发系统设计"],
  "optimization_points": [
    "在项目描述中突出RAG系统的检索准确率提升数据",
    "量化Agent调用的日活用户数"
  ],
  "highlight": "候选人RAG+Agent实战经验是岗位核心需求",
  "risk": "大厂竞争激烈，建议同时投递中厂备选"
}
```

Write all results to `{run_dir}/deep_results.json`:

```json
{
  "version": "1.0",
  "analyzed_by": "claude-code",
  "analyzed_at": "2026-08-10 14:30:00",
  "results": [/* per-job analysis array */]
}
```

### Phase 3: Merge & Report

```bash
python scripts/run_matcher.py --mode deep --merge --output-dir {run_dir}
```

Script auto-completes: read candidates + deep results → blended scoring (rule 40% + Claude 60%) → reclassify → HTML report.

HTML report shows `🧠 深度模式` badge. Deep-analyzed job cards show highlight/risk tags. "View optimization suggestions" button opens a modal with Claude-generated advice, missing skills, and risk assessment.

---

## Classification Tiers (Both Modes)

| Tier | Label | Meaning |
|------|-------|---------|
| `qualified` | 🟢 符合要求 | High match, ready to apply |
| `need_optimization` | 🟡 可优化后投递 | Meets requirements but competitive; optimize first |
| `cannot_apply` | 🔴 不可投递 | Hard requirements not met (education, experience gap) |

---

## Scoring Dimensions (Rule-Based, 0-115 pts)

1. **Education match** (25 pts) — degree level vs job requirement
2. **Experience match** (25 pts) — total years vs job requirement
3. **Skills match** (35 pts) — keyword overlap between resume skills and JD
4. **Salary match** (15 pts) — salary expectation vs offered range
5. **City match** (10 pts) — expected city vs job location
6. **Industry bonus** (5 pts) — industry keyword alignment
