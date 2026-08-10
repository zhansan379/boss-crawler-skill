# Script & Package Reference

## boss_crawler/ Package

Crawl logic split into independent modules. Import via `from boss_crawler import X`.

| Module | Path | Purpose |
|--------|------|---------|
| Config/constants | `boss_crawler/config.py` | Constants, filter maps, Chrome config, `SleepConfig` |
| Utilities | `boss_crawler/utils.py` | Filter helpers, file I/O, time estimation, param expansion |
| Data loading | `boss_crawler/data_loader.py` | JSON loading, position/city lookup, CSV dedup |
| Auth | `boss_crawler/auth.py` | Login detection, captcha check, `ensure_login()` |
| CLI parsing | `boss_crawler/cli.py` | argparse setup |
| State | `boss_crawler/state.py` | `TimeStats` + `StepManager` |
| Menu | `boss_crawler/menu.py` | Interactive menu functions (14 total) |
| Crawl engine | `boss_crawler/crawler.py` | Pagination, detail collection, flow orchestration |

### Key Functions

| Function | Module | Description |
|----------|--------|-------------|
| `ensure_login()` | `auth` | Phase 1a: open browser, single login check. `[LOGIN_OK]` or `[LOGIN_NEEDED]` |
| `check_login_status(page)` | `auth` | XPath-based login detection. Returns `True`/`False` |
| `check_page_status(page, response)` | `auth` | Composite page status: `need_login`/`no_data`/`normal`/`verify` |
| `crawl_jobs_by_query(dp, ...)` | `crawler` | Keyword-based paginated crawl |
| `crawl_jobs_by_position(dp, ...)` | `crawler` | Category-code-based paginated crawl |
| `crawl_job_details(dp, ...)` | `crawler` | Batch detail page crawl |
| `run_crawl_cli(args)` | `crawler` | CLI mode full crawl flow |
| `parse_args()` | `cli` | CLI argument parsing |
| `load_position_data()` | `data_loader` | Load position category JSON |
| `load_city_data()` | `data_loader` | Load city list JSON |
| `update_json_data()` | `data_loader` | Online update of position and city data |

### Python API Example

```python
from boss_crawler import (
    parse_args, run_crawl_cli, ensure_login,
    load_position_data, load_city_data,
)

# CLI mode
args = parse_args()
run_crawl_cli(args)

# Standalone login
ensure_login()

# Data access
positions = load_position_data()
cities = load_city_data()
```

---

## resume_matcher/ Package

Resume matching toolkit. No external LLM API dependency. Import via `from resume_matcher import X`.

| Module | Path | Purpose |
|--------|------|---------|
| Config/dataclasses | `resume_matcher/config.py` | Constants + `ResumeProfile`, `JobRequirements`, `MatchResult`, `JobClassification`, `DifficultyPrediction` |
| Utilities | `resume_matcher/utils.py` | `parse_education_level()`, `parse_experience_years()`, `parse_salary_range()`, `parse_company_size()`, `ensure_output_dir()` |
| File parsers | `resume_matcher/parsers.py` | `parse_resume_file()`, `parse_pdf()`, `parse_docx()` |
| Prompts | `resume_matcher/prompts.py` | `load_prompt()`, `get_resume_parse_prompt()`, `get_job_analysis_prompt()`, `get_match_analysis_prompt()`, `get_optimize_prompt()` |
| Data loading | `resume_matcher/data_loader.py` | `list_available_job_files()`, `load_job_data()` |
| Scoring | `resume_matcher/scoring.py` | `analyze_job_requirements_quick()`, `score_job_advanced()` (6 dimensions), `classify_jobs_advanced()` (4 tiers), `predict_difficulty()` |
| HTML report | `resume_matcher/report.py` | `generate_html_report()`, `generate_bauhaus_json()` |
| Auto-apply | `resume_matcher/auto_apply.py` | `auto_apply_jobs()` |
| Templates | `resume_matcher/templates/report.html` | HTML report template (dual-theme CSS) |

### Key Scripts

| Script | Purpose |
|--------|---------|
| `boss_post_interactive.py` | Crawler CLI entry point (thin wrapper over `boss_crawler/`) |
| `run_matcher.py` | Matching pipeline entry point (`--mode quick\|deep`) |
| `validate_profile.py` | Cross-validation: diff raw resume vs profile.json |

---

## prompts/ Directory

Template files (`.st`) — Claude reads and uses directly:

| Template | Purpose |
|----------|---------|
| `resume_parse.st` | Resume parsing prompt |
| `job_analysis.st` | Job requirement analysis prompt |
| `match_analysis.st` | Match analysis prompt (deep mode Phase 2) |
| `resume_optimize.st` | Resume optimization prompt |

---

## Output Directory Structure

All outputs under `assets/<timestamp>/`:

| File | Description |
|------|-------------|
| `profile.json` | Structured resume parse result |
| `resume_text.txt` | Raw resume text (for cross-validation) |
| `deep_candidates.json` | Deep mode Phase 1 output: top N candidates |
| `deep_results.json` | Deep mode Phase 2 output: Claude's per-job analysis |
| `scored_jobs.json` | Full job rule-scoring results (quick mode) |
| `job_classification.json` | Structured classification result |
| `matching_report.html` | HTML visualization report |
| `resume_{company}_{position}.md` | Per-job optimized resume |
| `apply_log.json` | Auto-apply record |
| `LATEST.txt` (in `assets/`) | Pointer to most recent run timestamp |
