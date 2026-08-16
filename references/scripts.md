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
| `load_position_data()` | `data_loader` | Load position category JSON (auto-inits on first use, see below) |
| `load_city_data()` | `data_loader` | Load city list JSON (auto-inits on first use, see below) |
| `update_json_data()` | `data_loader` | Online update of position and city data |

### First-Use Auto-Init

`post_data.json` / `weizhi.json` are not in the repo — they are fetched from zhipin.com. When either is
missing or corrupt, the loader calls `update_json_data()` once and re-reads, so a fresh clone crawls
without a manual `--update-data` step.

The retry is capped at **one browser launch per process** (`_auto_init_done`). If the file is still
unreadable afterwards the loader returns the same empty structure as before (`{}` /
`{'hot': [], 'other': []}`) and tells the user to run `--update-data` by hand — a second launch would
only re-hit whatever network or API problem broke the first one.

### CSV Schema

The CSV is the contract between crawler and matcher, so `CSV_FIELDS` is duplicated in
`boss_crawler/config.py` and `resume_matcher/config.py` and **must stay byte-identical** — the
matcher reads columns by name and silently sees empty strings for any it doesn't know about.

```python
CSV_FIELDS = [
    'link', '职位', '城市', '区域', '商圈', '地址', '公司', '薪资', '经验', '学历',
    '领域', '性质', '规模', '技能标签', '福利标签', '位置', '岗位要求和职责', '公司信息',
    '已失效', '代招',
    'HR活跃度', 'HR在线', 'HR职位', 'HR姓名', 'HR公司',
]
```

`地址`, `已失效`, `代招`, `公司信息` and all five `HR*` columns come from the detail API
(`zpData.jobInfo` / `brandComInfo` / `bossInfo`) and are empty without `-d`. `已失效` / `代招` /
`HR在线` are tri-state via `_tri_state()`: `'是'` / `'否'` / `''` where empty means **未采集**, not
`否`. `HR公司` is the HR's own employer and can differ from `公司` — that combination (or `代招=是`)
means a headhunter posting.

**Only append** new columns at the end: the crawl phase writes rows in `'a'` append mode, so
`init_csv_file` migrates an existing file's header to this exact order before any row is written.
Without that migration, new 25-column rows would land under an old 20-column header and shift
silently. Old CSVs stay readable — `csv.DictWriter(restval='')` and `DictReader` yield `''` for
absent columns, which renders as 未采集.

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

Resume matching toolkit. All model calls go through `scripts/llm/` (any OpenAI-compatible endpoint).
Import via `from resume_matcher import X`.

| Module | Path | Purpose |
|--------|------|---------|
| Config/dataclasses | `resume_matcher/config.py` | Constants + `ResumeProfile`, `JobClassification` |
| Utilities | `resume_matcher/utils.py` | `parse_experience_years()`, `parse_company_size()`, `ensure_output_dir()`, `print_header()`, `print_section()` |
| File parsers | `resume_matcher/parsers.py` | `parse_resume_file()`, `parse_pdf()`, `parse_docx()`, `parse_plain_text()` |
| Prompts | `resume_matcher/prompts.py` | `load_prompt()`, `get_resume_parse_prompt()`, `get_match_analysis_prompt()`, `get_greeting_prompt()`, `get_optimize_prompt()`, `get_crawl_params_prompt()` |
| Data loading | `resume_matcher/data_loader.py` | `list_available_job_files()`, `load_job_data()` |
| Scoring | `resume_matcher/scoring.py` | `score_job_advanced()` (6 dimensions, 0-115), `classify_jobs_advanced()` (4 tiers), `compute_difficulty()`, `tiers_to_classification()`, `build_job_view()` (the only field mapper), `hr_activity_rank()` / `hr_activity_sort_key()` (sort only, never scored) |
| Deep analysis | `resume_matcher/deep_analysis.py` | `save_deep_candidates()`, `merge_deep_results()` (blends rule 40% + LLM 60% by `rank`, writes `qualified_jobs.json`) |
| HTML report | `resume_matcher/report.py` | `generate_html_report()`, `generate_bauhaus_json()` |
| Auto-apply | `resume_matcher/auto_apply.py` | `auto_apply_jobs()`, `generate_greeting()`, `has_wasted_preview()` |
| Templates | `resume_matcher/templates/report.html` | HTML report template (dual-theme CSS) |

---

## llm/ Package

Every model call in this skill goes through here — one HTTP client against any OpenAI-compatible
`/chat/completions` endpoint. No vendor SDK, no dependency on any particular AI client.

| Module | Purpose |
|--------|---------|
| `llm/config.py` | Three-layer precedence: CLI flags > env (`LLM_*`, then `OPENAI_*`) > `assets/llm_config.json` > defaults. Per-stage overrides under `stages.{parse,infer,deep,greeting,resume}`. `require_key()` raises `ConfigError` with setup instructions rather than letting a 401 surface. Warns on unknown config keys instead of ignoring them |
| `llm/client.py` | `chat()`, `chat_json()`, `map_concurrent()`, `strip_fence()`, retry/backoff, usage accounting (`format_usage()`) |
| `llm/__init__.py` | The public surface the five inference points import |

`assets/llm_config.json` is gitignored; `assets/llm_config.example.json` is the template to copy.
**The key is masked in every printed line** — keep it that way.

### Key Scripts

| Script | Purpose |
|--------|---------|
| `pipeline.py` | The driver: `parse → infer → crawl → match → deep → merge → materials → render`. `--from X` runs one stage (`match` pulls `deep`+`merge`), `--to`/`--all` runs a range. **Never runs `apply.py`** — `test_pipeline.py` locks that. Exit codes: 0 ok, 1 precondition/total failure, 2 usage, 3 partial |
| `llm_check.py` | LLM config diagnosis, key masked. `--no-call` is free and is the precondition probe to run before a long flow; `--stage deep` shows one stage's effective config. 0 = usable, 1 = missing/invalid, 2 = configured but unreachable |
| `boss_post_interactive.py` | Crawler CLI entry point (thin wrapper over `boss_crawler/`) |
| `parse_resume.py` | Resume file → `resume_text.txt` + `profile.json` in a fresh run directory |
| `infer_params.py` | `profile.json` → `crawl_params.json`. Flags you pass are used verbatim; only the fields you omit are inferred. `--scale` is never inferred |
| `run_matcher.py` | Matching entry point (`--mode quick\|deep`, `--merge`) |
| `deep_analyze.py` | `deep_candidates.json` → `deep_results.json`, one concurrent request per candidate. `--resume` tops up missing ranks; exit 3 = partial |
| `gen_materials.py` | Per-job greeting + optimized resume into `generated/`. `--greeting-mode {ai,default,skip}`, `--resume-mode {ai,skip}`, `--only`, `--force`. Skips existing non-empty artifacts, which is what makes a pre-written custom greeting survive |
| `render_images.py` | `generated/resume_*.json` → `<姓名>-<应聘岗位>.png`. Serial by design (one browser, one `localStorage`); refuses to run when port 9333 holds a browser whose profile isn't `assets/showcv_profile` |
| `apply.py` | The only irreversible step. Dry run by default (no browser at all); `--yes` sends. Refuses on a missing greeting or a missing/blank attachment |
| `validate_profile.py` | Cross-validation: diff raw resume vs profile.json |
| `check_artifacts.py` | One-shot snapshot of `generated/`: does every selected job have a non-empty greeting and resume. No polling — `gen_materials.py` is synchronous, so nothing can land after it returns. 0-byte files count as missing. Exit 1 + the names, plus the `--only` command to repair them |
| `read_thin.py` | Thin views of the big JSON files (`--kind jobs\|profile\|deep`) so a full JD dump never enters context. Takes a **file** path |
| `stage_timer.py` | Stage timing telemetry → `{run_dir}/run_timings.jsonl`. Every stage instruments itself via `span`/the `stage()` context manager, which still writes on exception (`status=error`); `mark` records a bare boundary. `report` prints a duration ranking. Exists so the next optimization reads a file instead of forensically parsing a session transcript |
| `write_application_md.py` | Write `applications/<公司>-<岗位>/岗位信息+招呼语.md` with all 25 crawled fields + the greeting. Re-reads the original crawl CSV by `link`, because `build_job_view()` drops `区域` `商圈` `领域` `性质` `位置` `地址` `已失效` `代招` `HR姓名` `HR公司` and truncates `公司信息` / JD. Flags 已失效 and 代招 岗位 with a banner. Exit 1 when a job's CSV row can't be found |
| `verify_image.py` | Check a rendered resume PNG in ~8 lines of numbers: blank/solid detection, content-row count, bottom-margin overrun, and a cross-check against the sibling `.md`. **Use this instead of `Read` on the image** — one 0.5 MB PNG cost 639k input tokens (79% of a whole session's fresh input) and forced a compaction. `--all` sweeps an `applications/` tree |
| `where_am_i.py` | Infer the current stage from the run directory's artifacts and print the next commands in ~1k chars. The recovery move after a context compaction — reconstructing state by re-reading the 25k-char `auto-apply.md` is what made that run expensive. Read-only, always exits 0 |
| `preferences.py` | Crawl/matching parameter preset at `assets/preferences.json` (`show` / `missing` / `save` / `crawl-args` / `clear`). Path C runs `show` and branches on the exit code (1 = none), so a returning user skips the parameter questions entirely — the largest single time saving for a repeat run. One preset, overwrite on save, never expires (`show` just reports its age). `load()` drops any key outside `ALLOWED_KEYS` and any wrong-typed value: the file is hand-editable, and that whitelist is what stops `{"auto_apply": true}` from becoming an off switch for the `gate:send` confirmation. There is deliberately no field for jobs, greeting, or send |

### Tests

All offline — no network, no browser, no API key. Run any one directly with `python scripts/<name>.py`.

| Script | Covers |
|--------|--------|
| `test_pipeline.py` | Stage orchestration. The load-bearing assertion is the last one: **reaching the end of the pipeline still never executes `apply.py`**. Also stage ranges, stop-on-failure, quick mode's apply-pool backfill, and refusing to continue on an empty crawl |
| `test_apply_gate.py` | One core assertion: **without `--yes`, `auto_apply_jobs` is called exactly 0 times.** Plus "missing materials means no send" |
| `test_llm_client.py` | The client with `requests` swapped for a scripted fake, so it runs offline: JSON extraction from all three model output shapes (bare / fenced / prose-wrapped), retry/backoff, concurrency, usage accounting |
| `test_gen_materials.py` | Three silent-failure seams: the filename contract (`{kind}_{i}_` prefix and the `glob` that reads it back — a `[` in a company name breaks it), skip-vs-overwrite, and partial-failure exit codes |
| `test_deep_analyze.py` | The downstream seam: `deep_results.json` maps back by `rank`, and a rank mix-up assigns A's analysis to B with no error anywhere |
| `test_infer_hint.py` | A cross-process contract: `pipeline.py` sets `BOSS_PIPELINE_STAGE`, `infer_params.py` reads it to decide which repair command to print. Also that the printed path actually resolves from the cwd |
| `test_infer_prompt.py` | The crawl-params prompt and that the other four `.st` templates are still loadable and wrapper-callable |
| `test_where_am_i.py` | Stage inference from artifacts: monotonic progress, quick mode never asking for deep/merge, an empty pool getting its own branch, corrupt JSON, and that the printed tail never includes `apply.py --yes` |
| `test_stage_timer.py` | Same-name stage merging, and that a telemetry failure never crashes the business logic it wraps (an exception still records `status='error'` and propagates) |
| `test_job_view.py` | `build_job_view()` field parity against `CSV_FIELDS` |
| `test_null_profile.py` | JSON nulls in `profile.json` (`salary_expectation`, `total_years`, and per-category `skills`). All four paths crash on `get(key, default)` because the key *exists* with a null value — the rule is `or`, never the second arg to `get` |
| `test_crawl_dedup.py` | Run-scoped link dedup across keywords, the ≥80%-duplicate page skip, and `load_job_data()`'s fallback dedup. The load-bearing case is `test_resume_deeper_crawl_not_interrupted`: the skip must key on `run_dups` (seen elsewhere *this run*), never on total `skipped` — a file with 200 existing rows makes page 1 of a deeper re-crawl 100% duplicates, and keying on `skipped` would break out before reaching any new page |
| `test_ensure_login.py` | One command serving two callers whose endings must differ: a human at the keyboard needs "press Enter and I'll re-check", a script needs a parseable `[LOGIN_OK]` / `[LOGIN_NEEDED]` |
| `test_preferences.py` | Preset round-trip, `crawl_args()` flag mapping, graceful degradation (missing / corrupt / non-dict file → `{}` → "go ask the user", never a crash), and `age_days`. Group [3] `test_unknown_keys_dropped` is a security lock, not a typo check: it writes `auto_apply` / `send_without_confirm` / `skip_gate_send` / `greeting` / `jobs` straight into the JSON and asserts `load()` drops them all, that `set(prefs) <= set(ALLOWED_KEYS)`, and that the CLI help exposes no `--auto-apply` / `--send` / `--greeting` / `--jobs` flag. If you add a preset key, this is the test that must still pass |
| `test_greeting.py` | The greeting rules. Two things it stops from being reverted: (1) the first 15 characters must not be a pleasantry — `has_wasted_preview()` catches `您好，我是…`, which is what the old template opened with and what makes a message invisible in HR's list; (2) 到岗/时长/出勤 must not be invented — a profile with no `availability` (missing key, explicit `null`, or a hand-corrupted non-dict) must produce a greeting containing none of 到岗/可实习/每周/随时 |

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

Template files (`.st`), loaded by the scripts via `resume_matcher.prompts` and sent to the configured
endpoint. **These files are the single copy of their rules** — do not paraphrase them elsewhere.

| Template | Loaded by | Purpose |
|----------|-----------|---------|
| `resume_parse.st` | `parse_resume.py` | Resume parsing prompt. `basic_info.availability` (到岗时间 / 可实习时长 / 每周出勤) must be `null` unless the resume states it outright — those three go straight into a greeting as a commitment the employer schedules around, so inferring one from a graduation year is a broken promise, not a guess |
| `crawl_params.st` | `infer_params.py` | Crawl-parameter inference from a profile. Only the fields the caller left out are filled |
| `match_analysis.st` | `deep_analyze.py` | Per-job match analysis (deep mode). `highlight` / `risk` are **not** in here — the script appends them as a short addendum, so the template stays the scoring contract that `merge_deep_results` reads |
| `resume_optimize.st` | `gen_materials.py` | Resume rewrite. No-fabrication rule; `optimized_resume` starts at the first content section (no name, no contact line) and uses `##` as its top heading level |
| `greeting.st` | `gen_materials.py` | The greeting prompt. Holds the whole writing standard: the **first-15-character** rule (BOSS's message-list preview shows only that much, so it is a headline, not a greeting), the per-scenario formulas for those 15 characters, quantified-results-over-self-assessment, the 校招-vs-社招 split on whether to mention 出勤 at all, and the no-fabrication rule for 到岗/时长/出勤 |

---

## Output Directory Structure

All outputs under `assets/<timestamp>/`:

| File | Description |
|------|-------------|
| `resume_text.txt` | Raw resume text (for cross-validation) |
| `profile.json` | Structured resume parse result |
| `profile_validation.json` | `validate_profile.py`'s findings: dictionary-based misses plus regex hints |
| `crawl_params.json` | `infer` output — crawl argv source **and** where `match_mode` / `top_n` / `min_count` live |
| `crawl_summary.json` | Written only after a crawl really finished; its absence means nothing was crawled |
| `deep_candidates.json` | Deep mode pre-filter output: top N candidates |
| `deep_results.json` | Deep mode per-job analyses from `deep_analyze.py`, keyed by `rank` |
| `scored_jobs.json` | Full job rule-scoring results (quick mode) |
| `job_classification.json` | Structured classification result |
| `matching_report.html` | HTML visualization report |
| `qualified_jobs.json` | The apply pool (符合 + 需优化) in raw crawled fields. Its 1-based order is the alignment key for every artifact below — **never hand-edit it**, use `--only` |
| `generated/greeting_{i}_{公司}.txt` | Per-job greeting |
| `generated/resume_{i}_{公司}.json` | Per-job optimized-resume JSON (the whole `resume_optimize.st` object) |
| `showcv_staging/` | Temp Markdown staged for the render step, safe to delete |
| `showcv_exports/` | Raw ShowCV download (png or zip) |
| `applications/<公司>-<岗位>/` | Per-job archive: `岗位信息+招呼语.md`, `<姓名>-<应聘岗位>.md`, `<姓名>-<应聘岗位>.png` |
| `run_timings.jsonl` | Per-stage timings (`stage_timer.py`); `report` ranks them |
| `apply_log.json` | Auto-apply record |
| `LATEST.txt` (in `assets/`) | Pointer to most recent run timestamp |

Not run outputs, but also under `assets/` (all gitignored):

| Path | Description |
|------|-------------|
| `llm_config.json` | LLM endpoint/model/key. Copy `llm_config.example.json` and fill it in |
| `preferences.json` | Crawl/matching parameter preset (`preferences.py`) |
| `chrome_user_data/` | BOSS Zhipin login state (persists across runs) |
| `showcv_profile/` | Resume editor's Chrome profile — **the resumes live in here** (~28MB) |
| `showcv-resume.json` | Default `storage.py dump` target |
| `showcv_exports/` | Default `export_images.py` download directory |
| `showcv_backups/` | Automatic pre-delete backups from `delete_resumes.py` |

