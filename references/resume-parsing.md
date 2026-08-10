# Resume Parsing Reference

## JSON Output Schema

Claude reads `scripts/prompts/resume_parse.st` and outputs this structure:

```json
{
    "basic_info": {
        "name": "...", "phone": "...", "email": "...",
        "gender": "男/女", "age": null,
        "expected_city": "...", "expected_position": "..."
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

## Stage 3.5b: Cross-Validation Procedure (MANDATORY)

This is a quality gate. Skipping it risks profile omissions that cascade into incorrect match classifications.

### Step 1: Run Automated Check

```bash
python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json
```

The script extracts suspected technical terms (CamelCase names, known frameworks/tools) from the raw resume and diffs against `profile.json` skills, projects, and experience — outputting a gap report.

### Step 2: Claude Manual Line-by-Line Scan

Even if the script reports "no differences", Claude must:

1. **Reverse scan**: read the original resume, identify every technical term paragraph-by-paragraph, verify each appears in `profile.skills.*`
2. **Project completeness**: verify every project appears in `profile.projects` with complete `tech_stack` and all original `highlights`
3. **Experience completeness**: verify every work/internship entry appears in `profile.experience.companies` with `highlights` split per original bullet
4. **Awards & publications**: verify all awards, publications, and social links are extracted
5. **Skills description**: verify `skills.summary` preserves the original skills paragraph
6. **Soft evidence**: verify `profile.projects` is non-empty, `profile.skills` covers every skills paragraph, `profile.keywords` covers core competencies

### Step 3: Report Findings

```
🔍 Profile Cross-Validation Report

✅ Matched: Python, Java, LangChain, Django, Flask, Git, Docker, Linux, MySQL, Redis
⚠️ Omitted (auto-fixed): SpringBoot, MyBatis, Vue3, FastAPI, LangGraph, MCP
📝 Added projects: 华为商城AI客服, 大学生在线面试平台
📝 Added experience: 山西物联工业自动化技术研究院 2025.07-2025.10

Result: 3 omissions fixed, profile updated.
```

### Step 4: User Confirmation

Use `AskUserQuestion` to show the final profile summary and get user approval before proceeding.

---

## Stage 3.5: Resume → Crawl Parameter Inference (Path C)

Map resume fields to crawl CLI arguments:

| Resume field | → CLI param | Logic |
|-------------|------------|-------|
| `basic_info.expected_position` | `-p` | **Primary signal**: candidate's stated target position |
| `skills.programming` + `skills.frameworks` | `-p` | Combine language + framework as search keywords (e.g., Python+Django → `"Python,Python后端"`) |
| `experience.companies[0].position` | `-p` (supplement) | Most recent job title as supplementary keyword |
| `education.major` | `-p` (auxiliary) | CS → dev roles; Data Science → data analyst roles |
| `basic_info.expected_city` | `-c` | **Direct use**: candidate's stated target city |
| `skills.other` (industry terms) | `-p` (refinement) | Finance, healthcare, education → append as industry qualifiers |
| `salary_expectation` | `-s` | Map to nearest bracket: 10-20K → `10-20K`, 20-30K → `20-50K`. Choose slightly lower bracket for broader coverage |
| `experience.total_years` | `-e` | 1-3yr → `1-3年`; consider including one level down (e.g., 5yr → `3-5年,5-10年`) |
| `education.degree` | `-deg` | Bachelor's → `本科`; Master's → `本科,硕士` (downward compatible) |
| Resume mentions "实习"/"在校生" | `-j` | Set to `实习` |
| `awards` | (annotation only) | Mark as bonus points in match report, don't affect crawl params |
| Company size | `--scale` | Do NOT auto-infer; let user decide |

After inferring, **always present parameters to user and confirm with `AskUserQuestion` before crawling**. Users may adjust keywords, add cities, or modify filters.
