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
| `crawl_job_details(dp, ...)` | `crawler` | Batch detail page crawl. Backfills JD, company info, and the three HR-activity columns |
| `init_csv_file(path)` | `data_loader` | Write header, or rewrite an older header to the current `CSV_FIELDS` (see below) |
| `run_crawl_cli(args)` | `crawler` | CLI mode full crawl flow |
| `parse_args()` | `cli` | CLI argument parsing |
| `load_position_data()` | `data_loader` | Load position category JSON |
| `load_city_data()` | `data_loader` | Load city list JSON |
| `update_json_data()` | `data_loader` | Online update of position and city data |

### CSV Schema

The CSV is the contract between crawler and matcher, so `CSV_FIELDS` is duplicated in
`boss_crawler/config.py` and `resume_matcher/config.py` and **must stay byte-identical** — the
matcher reads columns by name and silently sees empty strings for any it doesn't know about.

```python
CSV_FIELDS = [
    'link', '职位', '城市', '区域', '商圈', '公司', '薪资', '经验', '学历',
    '领域', '性质', '规模', '技能标签', '福利标签', '位置', '岗位要求和职责', '公司信息',
    'HR活跃度', 'HR在线', 'HR职位',
]
```

The last five columns come from the detail API and are empty without `-d`. **Only append** new
columns at the end: the crawl phase writes rows in `'a'` append mode, so `init_csv_file` migrates an
existing file's header to this exact order before any row is written. Without that migration, new
20-column rows would land under an old 17-column header and shift silently.

### Adding a Job Field

A job is rebuilt once on its way from the CSV (Chinese column names) to the frontend (ASCII keys).
**`scoring.build_job_view()` is the only place that enumerates job fields** — add new fields there
and every output path picks them up:

```
CSV (中文列名)
  ├─ quick mode → classify_jobs_advanced
  │    ├─ tiers_to_classification ─→ generate_html_report → matching_report.html
  │    └─ generate_bauhaus_json ────→ job_classification.json
  └─ deep mode  → deep_analysis ────→ both of the above
                        ↑
              all three call build_job_view()
```

`build_job_view(job, fallback_category, *, company_info_len, jd_len, overrides)` reads both Chinese
and ASCII keys, so it accepts a raw CSV row or an already-mapped job. Callers that computed their
own values (deep mode's blended score) pass them via `overrides`; the JSON output only narrows the
truncation lengths.

This used to be **three independent whitelists** — one per path. Adding a field meant editing all
three, and missing one silently dropped it on that path with no error. HR activity was lost exactly
this way: the JSON was correct while every HTML card read 活跃度未采集 despite the data being
collected — worse than not showing it at all. `scripts/test_job_view.py` now locks the invariant:

```bash
python scripts/test_job_view.py   # asserts both artifacts carry identical, complete field sets
```

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
| Config/dataclasses | `resume_matcher/config.py` | Constants + `ResumeProfile`, `JobClassification` |
| Utilities | `resume_matcher/utils.py` | `parse_experience_years()`, `parse_company_size()`, `ensure_output_dir()`, `print_header()`, `print_section()` |
| File parsers | `resume_matcher/parsers.py` | `parse_resume_file()`, `parse_pdf()`, `parse_docx()` |
| Prompts | `resume_matcher/prompts.py` | `load_prompt()`, `get_resume_parse_prompt()`, `get_job_analysis_prompt()`, `get_match_analysis_prompt()`, `get_optimize_prompt()` |
| Data loading | `resume_matcher/data_loader.py` | `list_available_job_files()`, `load_job_data()` |
| Scoring | `resume_matcher/scoring.py` | `score_job_advanced()` (6 dimensions, 0-115), `classify_jobs_advanced()` (4 tiers), `compute_difficulty()`, `tiers_to_classification()`, `build_job_view()` (the only field mapper), `hr_activity_rank()` / `hr_activity_sort_key()` (sort only, never scored) |
| HTML report | `resume_matcher/report.py` | `generate_html_report()`, `generate_bauhaus_json()` |
| Auto-apply | `resume_matcher/auto_apply.py` | `auto_apply_jobs()` |
| Templates | `resume_matcher/templates/report.html` | HTML report template (dual-theme CSS) |

### Key Scripts

| Script | Purpose |
|--------|---------|
| `boss_post_interactive.py` | Crawler CLI entry point (thin wrapper over `boss_crawler/`) |
| `run_matcher.py` | Matching pipeline entry point (`--mode quick\|deep`) |
| `validate_profile.py` | Cross-validation: diff raw resume vs profile.json |
| `check_artifacts.py` | 7d→7e barrier: verify each subagent's artifact landed in `generated/`. Exit 1 + names on any miss. Use this instead of waiting on completion notifications, which are not guaranteed to arrive |

---

## showcv/ Scripts

Embedded ShowCV Markdown resume editor (path D / Stage 0). Standalone CLI scripts — run as
scripts, not modules. Full documentation in [resume-editor.md](resume-editor.md).

| Script | Purpose |
|--------|---------|
| `showcv/serve.py` | Static server for `app/` with SPA fallback; prints `SHOWCV_READY <url>` |
| `showcv/launch.py` | Opens isolated Chromium (port 9333, profile `assets/showcv_profile/`) and verifies load |
| `showcv/storage.py` | Resume data `dump`/`load`/`move` between origins and disk |
| `showcv/import_md.py` | Batch-import `.md` files as resumes (standalone stage, on request only) |
| `showcv/export_images.py` | Export resumes as PNG/zip via the `/export` direct link |
| `showcv/delete_resumes.py` | Delete resumes via `/delete`; backs up first, needs `--yes` |
| `showcv/sync_app.py` | Re-sync `app/` from a ShowCV repo `dist/` (upgrade only) |
| `showcv/_browser.py` | Shared browser connection logic, incl. real-profile resolution |
| `showcv/_resumes.py` | Shared resume list reader + `--id`/`--name` → id resolution |

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

Not run outputs, but also under `assets/` (both gitignored):

| Path | Description |
|------|-------------|
| `chrome_user_data/` | BOSS Zhipin login state (persists across runs) |
| `showcv_profile/` | Resume editor's Chrome profile — **the resumes live in here** (~28MB) |
| `showcv-resume.json` | Default `storage.py dump` target |
| `showcv_exports/` | Default `export_images.py` download directory |
| `showcv_backups/` | Automatic pre-delete backups from `delete_resumes.py` |

