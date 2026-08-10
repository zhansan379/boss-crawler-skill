---
name: boss-crawler
description: Crawls BOSS Zhipin job listings via DrissionPage, parses resumes, matches jobs against candidate profiles using rule-based scoring and LLM semantic analysis, generates HTML visualization reports, and auto-applies to matching positions. Use when the user wants to search BOSS Zhipin jobs, upload a resume for job matching, generate a job matching report, auto-apply to positions, or optimize a resume for specific job descriptions.
---

# BOSS Zhipin Job Crawling & Matching

Integrates job crawling (DrissionPage + Chrome CDP), resume parsing (Claude), job matching (rule-based pre-filter + LLM deep analysis), HTML reporting, and auto-apply into one end-to-end workflow.

All scripts live under `scripts/` relative to this skill directory.

## Path Selection

Three entry paths. Choose based on what data the user already has:

| Path | When | Flow |
|------|------|------|
| **A: Crawl-first** | No job data yet | Crawl → Parse resume → Match → Report → Apply |
| **B: Match-existing** | Already have job CSVs | Parse resume → Match → Report → Apply |
| **C: Resume-driven** ✨ | Have resume, want precision | Parse resume → Infer params → Crawl → Match → Report → Apply |

**Path C is recommended**: the resume tells you what to search — skills, expected city, salary range. Crawled jobs are naturally aligned with the candidate's background, yielding higher match rates than guessing keywords.

> **Confirmation rule**: regardless of path, Stage 7 (auto-apply) must show the recommended job list with match reasons BEFORE applying. Users confirm jobs, then greetings, then (optionally) resume image — only then execute apply.

## Run Directory

Every skill invocation creates a timestamped run directory under `assets/` (e.g., `assets/2026-08-10_14-30-00/`). All outputs go there. Within one conversation, reuse the same directory via `--output-dir`.

---

## Workflow

Copy this checklist and check off items as you complete them:

```
Progress:
- [ ] Stage 1: Crawl jobs (paths A, C)
- [ ] Stage 2: Read resume file
- [ ] Stage 3: Parse resume → profile.json
- [ ] Stage 3.5b: Cross-validate profile (MANDATORY gate)
- [ ] Stage 3.5: Infer crawl params from resume (path C only)
- [ ] Stage 4: Load job data from CSVs
- [ ] Stage 5: Match analysis (quick or deep mode)
- [ ] Stage 6: Generate HTML report + open in browser
- [ ] Stage 7: Confirm jobs → greetings → resume image → apply
- [ ] Stage 8: Resume optimization (optional)
```

### Stage 1: Crawl Jobs (paths A, C)

Two-phase CLI approach. See [references/crawl-commands.md](references/crawl-commands.md) for the full parameter table and more examples.

**Phase 1a — Ensure login:**

```bash
python scripts/boss_post_interactive.py --ensure-login
```

- Opens Chrome, checks login status via XPath (single detection, no polling)
- `[LOGIN_OK]` → browser closes, proceed to Phase 1b
- `[LOGIN_NEEDED]` → browser stays open, user logs in manually, tells Claude "已登录", Claude re-runs

**Phase 1b — Execute crawl:**

```bash
python scripts/boss_post_interactive.py -m custom -p "Python" -c "北京" -n 20 -d -y
```

Key flags: `-m custom` (keyword search, recommended), `-d` (include detail pages — critical for matching quality), `-y` (skip confirmation prompt). Login state persists via `./chrome_user_data/`.

### Stage 2: Read Resume

Ask for the resume file path. For PDF/Word, parse via:

```python
from resume_matcher import parse_resume_file
resume_text = parse_resume_file(file_path)
```

### Stage 3: Parse Resume

Claude reads `scripts/prompts/resume_parse.st`, fills in the resume text, and outputs structured JSON. See [references/resume-parsing.md](references/resume-parsing.md) for the complete JSON schema and extraction rules.

Create the run directory and save outputs:

```python
from resume_matcher import create_run_dir
from resume_matcher.deep_analysis import serialize_profile
import json, os

run_dir = create_run_dir()
# Save resume_text.txt and profile.json to run_dir
```

### Stage 3.5b: Cross-Validate Profile (MANDATORY)

Quality gate — must execute before any downstream stage. See [references/resume-parsing.md](references/resume-parsing.md) for the full 4-step procedure.

1. Run `python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json`
2. Claude manually scans original resume line-by-line against profile
3. Report findings, fix all omissions, update profile.json
4. Confirm with user via `AskUserQuestion`

### Stage 3.5: Infer Crawl Params (path C only)

Claude maps resume fields to crawl parameters. See [references/resume-parsing.md](references/resume-parsing.md) for the inference mapping table.

Present the inferred parameters to the user, confirm with `AskUserQuestion`, then execute Stage 1 crawl with the confirmed parameters.

### Stage 4: Load Job Data

```python
from resume_matcher import list_available_job_files, load_job_data
job_files = list_available_job_files()
csv_paths = [jf['path'] for jf in job_files]
jobs = load_job_data(csv_paths)
```

### Stage 5: Match Analysis

Ask the user to choose a mode. See [references/matching.md](references/matching.md) for mode details, scoring dimensions, and classification logic.

**Quick mode** — single command, rule-based 6-dimension scoring (0-115 pts), zero token cost:
```bash
python scripts/run_matcher.py --mode quick --profile {run_dir}/profile.json --output-dir {run_dir}
```

**Deep mode** — three phases, rule pre-filter + per-job LLM semantic analysis:
```bash
# Phase 1: Python pre-filter (ask user for top-N first)
python scripts/run_matcher.py --mode deep --profile {run_dir}/profile.json --top <N> --output-dir {run_dir}

# Phase 2: Claude reads deep_candidates.json, analyzes each job, writes deep_results.json

# Phase 3: Merge rule scores (40%) + Claude scores (60%), reclassify, generate report
python scripts/run_matcher.py --mode deep --merge --output-dir {run_dir}
```

### Stage 6: Generate HTML Report

```python
from resume_matcher import generate_html_report
html_path = generate_html_report(profile, classification, output_dir=run_dir)
```

Open in browser: `Invoke-Item {run_dir}\matching_report.html` (PowerShell) or `start {run_dir}/matching_report.html` (Bash).

### Stage 7: Confirm & Apply

Five sub-steps — never skip user confirmation. See [references/auto-apply.md](references/auto-apply.md) for greeting templates, fallback mechanism, and apply flow details.

- **7a**: Display recommended jobs table with match scores and reasons
- **7b**: `AskUserQuestion` — confirm which jobs to apply to
- **7c**: Generate per-job greetings (5-paragraph structure), user confirms/edits each
- **7c-2**: `AskUserQuestion` — upload resume image attachment?
- **7d**: Execute `auto_apply_jobs()` with confirmed greetings and optional resume image

### Stage 8: Resume Optimization (optional)

Claude reads `scripts/prompts/resume_optimize.st`, compares the resume against a specific job's JD, and suggests targeted improvements. Never fabricate experience. Save to `{run_dir}/resume_{company}_{position}.md`.

## Package Reference

See [references/scripts.md](references/scripts.md) for module tables, key functions, and import examples for both `boss_crawler/` and `resume_matcher/` packages.

## Key Principles

1. **Claude IS the LLM**: all AI analysis (resume parsing, job matching, optimization) uses Claude's own capabilities — no external LLM API
2. **Rule-first, then LLM**: Python rule-based scoring pre-filters; Claude deep-analyzes only top candidates
3. **Never fabricate**: resume optimization must not invent experience or skills
4. **Safe applying**: 3-5s between applications, max 10-20 per session, pause on captcha
5. **Always visualize**: generate and open HTML report for every run
