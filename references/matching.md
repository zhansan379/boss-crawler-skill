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
  "score": 85,
  "category": "qualified",
  "reason": "核心技能高度匹配，RAG/Agent经验是亮点",
  "education_match": {"score": 90, "match": true, "reason": "本科学历满足要求"},
  "experience_match": {"score": 80, "match": true, "reason": "2年与要求的3年接近"},
  "skills_match": {"score": 85, "matched": ["Python", "LangChain", "RAG"], "missing": ["K8s运维"], "reason": "核心AI技能匹配，缺少运维经验"},
  "salary_match": {"score": 90, "match": true, "reason": "20-30K 与期望 20-28K 重叠"},
  "missing_items": ["Kubernetes运维经验", "高并发系统设计"],
  "optimization_points": [
    "在项目描述中突出RAG系统的检索准确率提升数据",
    "量化Agent调用的日活用户数"
  ],
  "highlight": "候选人RAG+Agent实战经验是岗位核心需求",
  "risk": "大厂竞争激烈，建议同时投递中厂备选"
}
```

`category` must be one of the three English enums (`qualified` / `need_optimization` / `cannot_apply`).
The merge step also accepts the legacy `overall_match` + `classification` (Chinese label) fields for
backward compatibility with older `deep_results.json` files.

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

Every job carries a top-level **`application_category`** field — the single authoritative
verdict on whether to apply. The frontend renders it via lookup table only and performs no
arithmetic; changing thresholds never requires a frontend edit.

| `application_category` | Label | Meaning |
|------|-------|---------|
| `qualified` | 🟢 可直接投递 | Skills hit ≥4, salary within expectation, education + experience fully met |
| `need_optimization` | 🟡 优化后投递 | No hard gate tripped, but short of the `qualified` bar; gaps are closable |
| `cannot_apply` | 🔴 不可投递 | A hard gate tripped — see below |

The enum values match the `classification` container keys and `JobClassification` field names
exactly, so a job's bucket and its `application_category` can never disagree.

### Hard gates (the only paths to `cannot_apply`)

Evaluated **before** any score consideration — a hard gate rejects even on an otherwise perfect match:

1. **Education** — JD degree level > resume degree level (e.g. JD requires 硕士, resume is 本科)
2. **Experience** — gap ≥ `CANNOT_APPLY_EXP_GAP` (3) years
3. **Salary** — gap > `CANNOT_APPLY_SALARY_GAP` (8) K

Anything that trips no gate but misses the `qualified` bar is `need_optimization` — including the
middle band (experience gap 1–3 years, salary gap 3–8K). A job that tripped no hard gate is never
reported as "cannot apply".

### Edge cases

- **Salary 面议 / unparseable** — not disqualifying, but capped at `need_optimization`; unverified salary must not trigger Stage 7 auto-apply. Reason notes 薪资面议待确认.
- **Resume experience years unknown** — the ≥3-year gate cannot fire, and `qualified` is unreachable ("fully met" is unverifiable).
- **Resume degree missing** — assumed 本科 (`DEFAULT_RESUME_DEGREE_LEVEL`), preserving prior behaviour.

---

## Scoring Dimensions (Rule-Based, 0-115 pts)

1. **Salary match** (20 pts) — JD range vs expected range; overlap ≥2K scores full, "reach" jobs above expectation score higher than jobs below it
2. **Experience match** (20 pts) — JD required years vs actual years; 应届/经验不限 scores full
3. **Education match** (15 pts) — degree **level comparison**: JD ≤ resume scores full, JD > resume is a hard gate (0 pts)
4. **Skills match** (30 pts) — resume skills word-boundary-matched against JD + skill tags, 5 pts each (alias-normalized: `k8s`→`kubernetes`, `go`→`golang`, …)
5. **Position relevance** (20 pts) — target keywords found in the job title, 5 pts each
6. **AI bonus** (10 pts) — AI keywords in JD, tiered by de-duplicated hit count (≥5 → 10, ≥3 → 7, ≥1 → 4)

The score drives **`difficulty` only** (Easy / Medium / Hard, via `compute_difficulty()` at
`TIER1_MIN` 100 / `TIER2_MIN` 85). The apply verdict is decided independently by
`decide_application_category()` — score and category are deliberately decoupled so a high
score can never override a hard gate.

Thresholds all live in `scoring.py`: `QUALIFIED_MIN_SKILLS` (4), `NEED_OPT_MAX_SALARY_GAP` (3),
`NEED_OPT_MAX_EXP_GAP` (1), `CANNOT_APPLY_EXP_GAP` (3), `CANNOT_APPLY_SALARY_GAP` (8).
`NEED_OPT_MAX_*` only shape the reason wording; they do not affect classification.
