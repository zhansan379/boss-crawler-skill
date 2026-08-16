# Resume Parsing Reference

## JSON Output Schema

`parse_resume.py` fills `scripts/prompts/resume_parse.st` and writes the model's output as
`{run_dir}/profile.json`:

```json
{
    "basic_info": {
        "name": "...", "phone": "...", "email": "...",
        "gender": "男/女", "age": null,
        "expected_city": "...", "expected_position": "...",
        "availability": {
            "can_start": null, "duration": null, "days_per_week": null
        }
    },
    "education": {
        "school": "...", "degree": "...", "major": "...",
        "start_year": "...", "graduation_year": "..."
    },
    "experience": {
        "total_years": 0,
        "companies": [{
            "name": "...", "position": "...", "duration": "...",
            "type": "实习/全职", "description": "概述",
            "highlights": ["要点1（保留量化指标）", "要点2"]
        }]
    },
    "skills": {
        "programming": [], "frameworks": [], "tools": [], "other": [],
        "summary": "简历中技能描述的原文段落"
    },
    "projects": [{
        "name": "...", "description": "概述",
        "highlights": ["要点1（保留量化指标和架构细节）", "要点2"],
        "tech_stack": [], "url": null, "duration": "..."
    }],
    "awards": [{"name": "...", "level": "国家级/省级", "rank": "一等奖/二等奖", "year": "2024"}],
    "publications": [{"title": "...", "journal": "...", "year": "2026"}],
    "social_links": {"github": "...", "blog": "...", "other": []},
    "salary_expectation": {"min": 0, "max": 0},
    "keywords": ["..."]
}
```

## Extraction Rules

- **highlights**: never merge bullet points; preserve every original bullet with all quantitative metrics (percentages, seconds, counts)
- **awards / publications / social_links**: must be extracted; use `[]` or `{}` if absent
- **skills.summary**: preserve the original skills description paragraph verbatim — do not tag-ify or split
- **basic_info.availability**: `null` unless the resume states it outright. These three (到岗时间 / 可实习时长 / 每周出勤) get written straight into a greeting sent to a real HR, who schedules a desk and a start date around them — so a value inferred from a graduation year or a course load is a broken promise, not a lucky guess. Missing is fine; `greeting.st` has a formula that carries no availability claim, and `test_greeting.py` asserts nothing about 到岗 appears when the field is absent

## Saving Profile

```python
from resume_matcher import create_run_dir, ResumeProfile
from resume_matcher.deep_analysis import serialize_profile
import json, os

run_dir = create_run_dir()

# Save raw resume text (for cross-validation)
resume_text_path = os.path.join(run_dir, 'resume_text.txt')
with open(resume_text_path, 'w', encoding='utf-8') as f:
    f.write(resume_text)

# Build and save ResumeProfile
profile = ResumeProfile(
    basic_info=result['basic_info'],
    education=result['education'],
    experience=result['experience'],
    skills=result['skills'],
    projects=result['projects'],
    awards=result.get('awards', []),
    publications=result.get('publications', []),
    social_links=result.get('social_links', {}),
    salary_expectation=result['salary_expectation'],
    keywords=result['keywords'],
    raw_text=resume_text
)

profile_path = os.path.join(run_dir, 'profile.json')
with open(profile_path, 'w', encoding='utf-8') as f:
    json.dump(serialize_profile(profile), f, ensure_ascii=False, indent=2)
```

---

## Cross-Validation Procedure

`parse_resume.py` runs this automatically and writes `{run_dir}/profile_validation.json`; the parse
stage's exit code 1 comes from it. It is one command and usually ends in one line. It is not a
blocking gate, but the omissions it catches cascade into wrong match classifications downstream, so
don't skip past its output.

### Step 1: The Automated Check

```bash
python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json
```

The script scans the raw resume with a known-tech dictionary (`KNOWN_TECH_TERMS`) and diffs against
`profile.json` skills + project tech stacks. **Exit code 1 means a skill is missing, and nothing
else.** That dictionary lookup is the one signal here that is independent of the model which produced
the JSON, which is why it — and not a re-read — is the default check.

Everything printed under `hints` — unmatched project names, unmatched company names, skill categories
with fewer than two entries — comes from loose regex plus exact set-difference:

- `hint_projects` grabs "the line after a date range", so job titles and company names land there too
- `hint_companies` matches literally, so `山西物联…研究院` vs the same name with `有限公司` reads as a
  missing entry; a client or cloud vendor named inside a project description reads as missing work
  experience
- `hint_warnings` fires on any category with one item, which is often legitimate

Read the hints as places to look. Do not treat them as findings, and do not fix profile.json just to
silence one.

### Step 2: A Manual Scan — Only When It Is Warranted

The script only knows skills, and only the ones in its dictionary. A full read covers the rest, but
it costs the resume text plus `profile.json` in context, so do it **only** when one of these holds:

- exit code 1, or a hint that looks like a real omission rather than a wording difference
- the user asks for a thorough check
- a downstream match result is obviously wrong in a way that points at the profile

Then read `{run_dir}/resume_text.txt` (never the original PDF/Word file) and check:

1. **Skills beyond the dictionary**: every technical term appears in `profile.skills.*` — including
   terms `KNOWN_TECH_TERMS` has never heard of
2. **Project completeness**: every project appears in `profile.projects` with complete `tech_stack`
   and all original `highlights`
3. **Experience completeness**: every work/internship entry appears in `profile.experience.companies`
   with `highlights` split per original bullet
4. **Awards & publications**: all awards, publications, and social links are extracted
5. **Skills description**: `skills.summary` preserves the original skills paragraph
6. **Soft evidence**: `profile.projects` is non-empty and `profile.keywords` covers core competencies

A clean parse needs none of this. Re-reading a resume to confirm a validator that already passed is
context spent for no new information.

### Step 3: Report Findings

One line when nothing was wrong:

```
✅ Profile 交叉校验：exit 0，逐项扫描无遗漏（hints 2 条，均为措辞差异）
```

When something was fixed:

```
🔍 Profile Cross-Validation Report

✅ Matched: Python, Java, LangChain, Django, Flask, Git, Docker, Linux, MySQL, Redis
⚠️ Omitted (auto-fixed): SpringBoot, MyBatis, Vue3, FastAPI, LangGraph, MCP
📝 Added projects: 华为商城AI客服, 大学生在线面试平台
📝 Added experience: 山西物联工业自动化技术研究院 2025.07-2025.10

Result: 3 omissions fixed, profile updated.
```

### Step 4: User Confirmation (rarely)

Transcription fixes — a skill that was plainly in the resume and plainly missing from the JSON — need
no confirmation. Report and continue.

Use `AskUserQuestion` only when a correction was a *judgement call*: a skill the resume implies rather
than states, projects you merged or split, an experience entry whose dates or scope are ambiguous.
Show what you changed and why it was a call, not a copy.

---

## The `infer` Stage: Resume → Crawl Parameters

`infer_params.py` fills `scripts/prompts/crawl_params.st` and writes `{run_dir}/crawl_params.json`.
The crawl, match, deep and merge stages all read that file — a missing or wrong value here does not
error, it silently narrows or widens everything downstream. The mapping the prompt encodes:

| Resume field | → `crawl_params.json` key | Logic |
|-------------|------------|-------|
| `basic_info.expected_position` | `keywords` | **Primary signal**: candidate's stated target position |
| `skills.programming` + `skills.frameworks` | `keywords` | Combine language + framework as search keywords (e.g., Python+Django → `"Python,Python后端"`) |
| `experience.companies[0].position` | `keywords` (supplement) | Most recent job title as supplementary keyword |
| `education.major` | `keywords` (auxiliary) | CS → dev roles; Data Science → data analyst roles |
| `basic_info.expected_city` | `cities` | **Direct use**: candidate's stated target city |
| `skills.other` (industry terms) | `keywords` (refinement) | Finance, healthcare, education → append as industry qualifiers |
| `salary_expectation` | `salary` | Map to nearest bracket: 10-20K → `10-20K`, 20-30K → `20-50K`. Choose slightly lower bracket for broader coverage |
| `experience.total_years` | `experience` | 1-3yr → `1-3年`; consider including one level down (e.g., 5yr → `3-5年,5-10年`) |
| `education.degree` | `degree` | Bachelor's → `本科`; Master's → `本科,硕士` (downward compatible) |
| `education.graduation_year` vs today | `job_type` + `experience` | Still enrolled → `实习` + `在校生`; graduated → `全职`. Stated internship intent overrides the date arithmetic. Never leave `job_type` null — that mixes 实习 and 全职 into one scored pool |
| `awards` | (annotation only) | Mark as bonus points in match report, don't affect crawl params |
| Company size | (nothing) | Not inferable from a resume; the prompt is told not to output `scale` |
| (keyword count) × `cities` | `keywords` | Budget by market size — see below |

`match_mode`, `top_n` and `min_count` also come out of this stage: `match_mode` decides whether the
match stage pulls `deep`, `top_n` caps how many candidates the deep pass sends to the LLM, and
`min_count` is the crawl stage's "enough jobs" threshold.

**Keyword count is budgeted by the city's market size, not by how many you can think of.**
Crawl time is linear in keyword count, and in a small market extra keywords buy nothing but
duplicates: a 太原 run with 5 keywords produced 117 rows containing only 53 unique jobs (54%
duplicates). Budget:

| City | Keywords |
|---|---|
| 一线 / 新一线 (the `hotCitySites` list in `assets/weizhi.json`) | up to 5 |
| Everywhere else | 2-3 |

Within the budget, spend keywords on **distinct concepts, not synonyms**. `AI应用开发` and
`大模型应用开发` are one concept in any market small enough to matter — they return the same
pool. `Python` + `后端开发` + `数据分析` are three. The crawler now detects this at runtime
(it skips a keyword's remaining pages once a page is ≥80% jobs already collected this run, and
prints a 跨关键词重复 count in the summary), but a synonym still costs one wasted page load per
keyword — pick well up front.

After inferring, **always confirm with `AskUserQuestion` before crawling** — batched into two calls
plus the `min_count` follow-up, with the inferred set as the recommended first option so accepting
costs a single click. Then pass every confirmed value as a `pipeline.py --from infer` flag: fully
specified parameters mean the stage makes no model call at all. This gate stays even when the
inference looks unambiguous — the crawl runs through the user's own logged-in browser, wrong
parameters waste a long crawl, and nothing about it can be undone afterwards. See SKILL.md's `infer`
section for the exact flags and the accepted filter labels.
