---
name: boss-crawler
description: Crawls BOSS Zhipin job listings via DrissionPage, parses resumes, matches jobs against candidate profiles using rule-based scoring and LLM semantic analysis, generates HTML visualization reports, auto-applies to matching positions, and launches an embedded Markdown resume editor (ShowCV). Use when the user wants to search BOSS Zhipin jobs, upload a resume for job matching, generate a job matching report, auto-apply to positions, optimize a resume for specific job descriptions, or open the resume editor to write/edit a resume ("打开简历编辑器", "启动 ShowCV", "写一份简历", "预览简历").
---

# BOSS Zhipin Job Crawling & Matching

Integrates job crawling (DrissionPage + Chrome CDP), resume parsing (Claude), job matching (rule-based pre-filter + LLM deep analysis), HTML reporting, auto-apply, and an embedded Markdown resume editor into one end-to-end workflow.

All scripts live under `scripts/` relative to this skill directory.

## Path Selection

Four entry paths. Choose based on what data the user already has:

| Path | When | Flow |
|------|------|------|
| **A: Crawl-first** | No job data yet | Crawl → Parse resume → Match → Report → Apply |
| **B: Match-existing** | Already have job CSVs | Parse resume → Match → Report → Apply |
| **C: Resume-driven** ✨ | Have resume, want precision | Parse resume → Infer params → Crawl → Match → Report → Apply |
| **D: Editor-only** | No resume file yet, wants to write or edit one | Launch resume editor → **stop there** |

**Path C is recommended** for matching: the resume tells you what to search — skills, expected city, salary range. Crawled jobs are naturally aligned with the candidate's background, yielding higher match rates than guessing keywords.

**Path D terminates at launch.** It opens the editor and reports the URL — nothing else. Don't chain it into matching on your own. It commonly serves as a precursor: the user writes a resume in the editor, exports a PDF, then re-enters at path A/B/C. If they'd rather skip the PDF round-trip, the editor's stored Markdown can feed Stage 3 directly — see [references/resume-editor.md](references/resume-editor.md) — but only do that when the user asks.

> **Confirmation rule**: for paths A/B/C, Stage 7 must show the recommended job list with match reasons BEFORE applying, and must pass **two** user gates: 7bc (which jobs) and 7g (approve the generated materials). Never apply without both.
>
> **Presets never reach these gates.** `assets/preferences.json` covers crawl and matching parameters
> only — it has no field for which jobs, what greeting, or whether to send, and `load()` drops any key
> outside its whitelist. Merging questions saves typing; it never removes 7g, which is the only thing
> standing in front of an irreversible action.

## Run Directory

Every skill invocation creates a timestamped run directory under `assets/` (e.g., `assets/2026-08-10_14-30-00/`). All outputs go there. Within one conversation, reuse the same directory via `--output-dir`.

Path D is the exception — it produces no run outputs, so it needs no run directory.

---

## Workflow

Copy this checklist and check off items as you complete them:

```
Progress:
- [ ] Stage 0: Launch resume editor (path D — terminal step)
- [ ] Stage 1: Check preset (`preferences.py show`) → crawl jobs (paths A, C)
- [ ] Stage 2-3: Read resume file → parse → profile.json
- [ ] Stage 3.5b: Cross-validate profile (run the script, usually one line)
- [ ] Stage 3.5: Infer crawl params (path C, no preset) — TWO questions: (1) city+keywords+mode+TopN, (2) 经验+阶段+薪资+规模
- [ ] Stage 4-6: Match analysis (mode/TopN already known) → report written and opened
- [ ] Stage 7: 7bc one question (jobs+greeting+image) → parallel generation → 7g gate → apply
```

**Four stops, not six.** The 2026-08-14 run stopped to ask 6 times and spent 12 minutes (30%) on
interaction round-trips. The stops that remain: the resume file path, the Stage 3.5 confirmation
(two batched `AskUserQuestion` calls — skipped entirely when a preset exists), 7bc, and 7g. **7g is
never removed** — it is the only thing in front of an irreversible action.

**Lost your place (e.g. after a context compaction)? Do not re-read the reference docs to rebuild
state.** Ask the filesystem instead:

```bash
python scripts/where_am_i.py           # or pass an explicit <run_dir>
```

It infers the current stage from the run directory's artifacts and prints the next commands in
~500 characters. `references/auto-apply.md` is 25k characters; in the 2026-08-13 run it was read
five times, twice purely because compaction had discarded the earlier read. Consult a reference doc
only for the *one* section `where_am_i.py` points you at, and read that section, not the whole file.

**Three habits that keep a run cheap.** The 2026-08-13 run took 46 minutes to deliver a single
application; 65% of that was model inference across 97 round trips. The 2026-08-14 run hit context
compaction mid-way because the main agent read full `qualified_jobs.json` (52 lines with JDs and
company profiles), `shard_01.md` (190 lines of full JDs), and `resume_1_SmallRig.json` (34 lines of
optimized resume). All three rules below exist to keep bulk text out of *your* context, because
that is what triggers compaction:

1. **Fan bulk analysis out to subagents; keep only the verdicts.** Deep-mode Phase 2 is sharded for
   this reason (`scripts/shard_deep_candidates.py`) — N full JDs never enter the main context. When
   you dispatch agents, require a file write and a one-line reply; never let an agent echo its
   payload back.
2. **Never `Read` a rendered resume image.** Use `scripts/verify_image.py`. One 0.5 MB PNG cost
   638,960 input tokens — 79% of that session's fresh input, in a single tool call.
3. **Never `Read` full data files — use `read_thin.py` instead.** One `qualified_jobs.json` with
   full JDs and company descriptions is ~50 lines of context. One `shard_*.md` is ~190 lines. One
   `profile.json` is ~30 lines. The main agent only needs the "thin" fields: link, company, position,
   score, verdicts. For everything else, use `python scripts/read_thin.py`:

   ```bash
   python scripts/read_thin.py {run_dir}/qualified_jobs.json --kind jobs     # → table fields
   python scripts/read_thin.py {run_dir}/profile.json --kind profile         # → summary stats
   python scripts/read_thin.py {run_dir}/deep_results.json --kind deep       # → verdicts only
   ```

   **Shard files (`shard_*.md`) are for sub-agents only.** The main agent never reads them — it
   dispatches sub-agents, waits for the barrier, and proceeds to `--merge`. The sub-agents' output
   (`deep_results.json`) is your source of truth; trust it without re-reading the shards.

Drop a timing mark at each stage boundary so the next run can be profiled from a file rather than
from a session transcript:

```bash
python scripts/stage_timer.py mark <run_dir> stage_7d_dispatch
python scripts/stage_timer.py report <run_dir>      # duration ranking when done
```

### Stage 0: Launch Resume Editor (path D)

Serves the embedded ShowCV build (`app/`) locally and opens it in an isolated Chromium. No `pnpm install` or node needed. See [references/resume-editor.md](references/resume-editor.md) for design rationale, limitations, and the `storage.py` data-moving tool.

**Step 1 — start the static server** (background task):

```bash
python scripts/showcv/serve.py
```

Wait for the ready signal and read the actual address from it:

```bash
until grep -q "SHOWCV_READY" "<background task output file>"; do sleep 0.3; done
grep "SHOWCV_READY" "<background task output file>"
```

First line is always `SHOWCV_READY http://127.0.0.1:<port>`, default 3090.

**If that background process exits immediately but still printed `SHOWCV_READY`**: the service was already running and this run reused it. That's normal — use the address and continue. Do NOT restart it or pick another port; the port is what scopes the user's saved resumes.

**Step 2 — open the browser** (use the address from step 1, don't assume 3090):

```bash
python scripts/showcv/launch.py http://127.0.0.1:3090
```

Prints `url=` / `title=` / `profile=` on success, with `ShowCV` in the title. **If the title lacks `ShowCV` the script exits 1** — that means the build is incomplete or the server isn't up. Don't report success.

Optional flags: `--headless`, `--close` (verify then close immediately, for smoke tests), `--browser <exe>`.

**Step 3 — report to the user**: the URL, that the browser is open, and **how to stop it** — `TaskStop` on the step-1 background task; they close the browser window themselves.

Then stop. Path D ends here.

### Stage 0.5: Batch-Import Markdown (standalone, on request only)

Bulk-loads `.md` files into the editor's resume list. **Deliberately not wired into any path and
not in the Progress checklist** — run it only when the user asks to import Markdown files. It
assumes Stage 0 already ran (server up, browser open) and fails rather than starting them itself.

```bash
python scripts/showcv/import_md.py --url http://127.0.0.1:3090 <files-or-dirs> [-r] [--dry-run]
```

Read the URL from Stage 0's `SHOWCV_READY` line — `--url` has no default on purpose. Batching at
the frontend's 50-file limit, duplicate-name handling, and the `localStorage` verification are
handled by the script; see [references/resume-editor.md](references/resume-editor.md) for its
constraints and failure modes.

### Stage 0.6: Export Resumes as Images (standalone, on request only)

Drives the editor's `/export` direct link, which the export button cannot do: it takes repeated
`id` params or `all=1`, so one call covers a batch. **Not wired into any path and not in the
Progress checklist** — same standing as Stage 0.5, and it likewise assumes Stage 0 already ran.

```bash
python scripts/showcv/export_images.py --url http://127.0.0.1:3090 [--name NAME | --id ID | --all]
    [--mode paginated|flat] [--scale 1|2|3] [--out DIR] [--dry-run]
```

With no selector it exports the current resume, matching the page's own fallback. Names are
resolved to ids locally, so a typo fails before anything is exported. Output is one PNG per A4
page (`paginated`) or one long image (`flat`); anything more than a single image arrives as
`showcv-images-<date>.zip`. The script confirms the files actually reached disk rather than
trusting the page's "已下载" text.

### Stage 0.7: Delete Resumes (standalone, on request only)

Drives the `/delete` direct link. **Not wired into any path and not in the Progress checklist.**
Destructive: `localStorage` is the only copy of these resumes.

```bash
# always look first
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --dry-run
# then commit
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --yes
```

Without `--yes` it only prints the plan. With `--yes` it takes a full backup first (restorable via
`storage.py --force load`, and the command is printed), then goes through the site's own
confirmation page and aborts without clicking if the names it lists differ from what was resolved
locally. Unlike `/export`, a missing `id` is never taken to mean "the current resume" — see
[references/resume-editor.md](references/resume-editor.md).

### Stage 1: Crawl Jobs (paths A, C)

Two-phase CLI approach. See [references/crawl-commands.md](references/crawl-commands.md) for the full parameter table and more examples.

**Phase 0 — Check for a saved preset. Do this first, before asking anything.**

```bash
python scripts/preferences.py show      # exit 0 = preset found, exit 1 = none
```

Exit 0 → **do not ask.** State the parameters and their age in one line, then run the command the
script printed:

```
用上次的参数（74 天前存的）：太原 / AI应用开发,Python / deep / Top10 / 应届生·本科，开始爬取。
```

Presets never expire — the age is reported so the user can interrupt, not so you can gate on it.
Exit 1 → no preset, take the two confirmation questions in Stage 3.5 (core + filters), then save the
answer.

This is the single biggest saving for a returning user: the 2026-08-14 run stopped to ask 6 times,
and Stage 1 plus the two gates burned 12 minutes (30% of the run) mostly on interaction round-trips.

**Phase 1a — Ensure login:**

```bash
python scripts/boss_post_interactive.py --ensure-login
```

- Opens Chrome, checks login status via XPath (single detection, no polling)
- `[LOGIN_OK]` → browser closes, proceed to Phase 1b
- `[LOGIN_NEEDED]` → browser stays open, user logs in manually, tells Claude "已登录", Claude re-runs

**Phase 1b — Execute crawl:**

```bash
python scripts/boss_post_interactive.py -m custom -p "Python" -c "北京" -n 20 -d -y \
    --run-dir "{run_dir}"
```

Key flags: `-m custom` (keyword search, recommended), `-d` (include detail pages — critical for matching quality), `-y` (skip confirmation prompt). Login state persists via `assets/chrome_user_data/`.

**Always pass `--run-dir`** — it writes a real `crawl` span (with `status=error` if the round dies
partway) instead of leaving this stage to be inferred from the gap between two marks. Omitting it
silently degrades to no timing, and this stage is the most expensive one in the whole run.

### Stage 2-3: Read & Parse Resume

Reading the file and structuring it are one pass, not two stages. Ask for the resume file path, then:

```python
from resume_matcher import parse_resume_file, create_run_dir

resume_text = parse_resume_file(file_path)   # PDF / Word / md / markdown / txt
run_dir = create_run_dir()
# write resume_text.txt into run_dir
```

The resume text now enters your context once (~2-3 KB). That's unavoidable — you need it to write
the file. But the heavy Claude inference on the full text must NOT happen in the main context.
**Fan it out to a sub-agent:**

> **Dispatch a sub-agent** (type: general-purpose, no schema needed):
>
> 1. Read `{run_dir}/resume_text.txt` and `scripts/prompts/resume_parse.st`
> 2. Fill the template with the resume text, generate `profile.json`
> 3. Write `profile.json` to `{run_dir}/profile.json`
> 4. Reply: `done {run_dir}/profile.json — {skills_count} skills, {experience_count} experiences`
>
> The sub-agent handles the full resume text in its own context. You receive only the one-line
> summary.

See [references/resume-parsing.md](references/resume-parsing.md) for the complete JSON schema and
extraction rules.

### Stage 3.5b: Cross-Validate Profile

Always run it; it is cheap and almost always ends in one line. See
[references/resume-parsing.md](references/resume-parsing.md) for the full procedure.

1. Run `python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json`
2. **If exit 0 and no hints worth reviewing**: done. Report in one line and move on.
3. **If exit 1 (missing skill) or serious hints**: **dispatch a sub-agent** to cross-validate.
   The sub-agent reads `resume_text.txt` + `profile.json`, checks for skills/experiences/projects
   the parse missed or misrepresented, fixes `profile.json` if needed, and replies with a one-line
   summary of changes. Only escalate to `AskUserQuestion` when the sub-agent flags a *judgement*
   call (a skill the resume implies but doesn't name, a project it merged or split).

**Do not replace step 1 with self-review.** `validate_profile.py`'s skill check is a dictionary
lookup (`KNOWN_TECH_TERMS`), so it is the one signal here that is *independent of the model that
produced the JSON*. The failure it catches is "the parse dropped a skill" — and the model that
dropped it is the least likely to notice. Step 2 complements the script, it doesn't substitute for it.

**Exit code 1 means a skill is missing — that alone.** Anything the script prints under `hints`
(unmatched project names, unmatched company names, thin skill categories) comes from loose regex and
exact set-difference, so a wording difference between resume and profile is enough to trigger it.
Read hints, don't obey them, and don't let one turn into a gate.

**The cross-validation sub-agent keeps the full resume text out of the main context.** The main
agent never reads `resume_text.txt` or `profile.json` in full. Use `read_thin.py --kind profile`
if you need to confirm a specific field.

### Stage 3.5: Infer Crawl Params (path C only)

Claude maps resume fields to crawl parameters. See [references/resume-parsing.md](references/resume-parsing.md) for the inference mapping table.

**Budget keywords by city, not by imagination.** Crawl time is linear in keyword count: 5 keywords
in 太原 returned 117 rows holding 53 unique jobs. Small market → 2-3 keywords; 一线/新一线 → up to 5.
And spend them on distinct concepts — `AI应用开发` and `大模型应用开发` are the same search.

Confirm in **two** `AskUserQuestion` calls. One call is capped at 4 questions, so the eight
params split as:

1. **爬取与匹配核心** — city, keywords (multi-select), matching mode (quick/deep), and deep's Top-N.
   All four are known before the crawl starts and none constrains the others, so one call.
2. **列表筛选** — experience (应届/不限), job type (校招/实习/社招), salary floor, and company scale.
   Also four questions in one call. First option in each is the candidate-appropriate default, so
   accepting is one click; leave a filter empty to skip it (crawl unfiltered on that axis).

Splitting is deliberate: eight questions will not fit one `AskUserQuestion`, and the four filters
are independent of the four core params — none constrains the others, so batching each set into one
call still saves the round-trips. The filters are optional and all-first-option-accept is a fast path.

Then save it, so the next run skips this entirely:

```bash
python scripts/preferences.py save --city 太原 --keywords "AI应用开发,Python" \
      --match-mode deep --top 10 --count 20 --degree 本科 \
      --experience "应届生" --job-type 实习,全职 --salary "5-10K"
```

The four filter flags (`--experience` / `--job-type` / `--salary` / `--scale`) are optional — omit any
you want left unfiltered. Accepted values are the Chinese labels in `boss_crawler/config.py`
(`EXPERIENCE_MAP` / `JOB_TYPE_MAP` / `SALARY_MAP` / `SCALE_MAP`), e.g. `应届生`, `实习`, `5-10K`.
They map straight onto the crawler's `-e -j -s --scale` flags. Note there is **no `校招` job-type code**
(`JOB_TYPE_MAP` is 全职/实习/兼职) — an unknown value is warned about and skipped by
`resolve_filter_values`, so don't put one in. `不限` is likewise skipped (means "no filter on that axis"),
so prefer omitting the flag to passing `不限`.

**Skip this whole stage when `preferences.py show` exited 0.** The saved parameters were confirmed by
the user in an earlier run — re-confirming them is the round-trip this preset exists to remove.
Announce what you're using (with its age) and crawl.

**When there is no preset, keep the gate even if the inference looks unambiguous.** The crawl is an
outward-facing action driven through the user's own logged-in browser: wrong parameters cost a long
crawl plus a batch of useless data, and there is nothing to undo afterwards. The ambiguity is rarely
just "the resume lists two cities" — expected salary, seniority, and which keyword to search (`Python`
vs `后端开发`) are all judgement calls. What gets collapsed here is rounds of typing, not the
confirmation itself.

### Stage 4-6: Match Analysis & Report

`run_matcher.py` does all three in one process — loads the CSVs, scores and classifies, then writes
`matching_report.html` and `scored_jobs.json` itself.

**Do not call `generate_html_report()` yourself.** It already runs inside the script, and after a CLI
run you don't hold the `classification` object it needs anyway.

**Do not ask for the mode or Top-N here.** Both arrived with Stage 3.5's confirmation or the saved
preset (`match_mode`, `top_n`). Asking again is the duplicate round-trip this consolidation removed.
Only ask if neither source has them. See [references/matching.md](references/matching.md) for mode
details, scoring dimensions, and classification logic.

**Quick mode** — single command, rule-based 6-dimension scoring (0-115 pts), zero token cost:
```bash
python scripts/run_matcher.py --mode quick --profile {run_dir}/profile.json --output-dir {run_dir}
```

**Deep mode** — three phases, rule pre-filter + per-job LLM semantic analysis:

```bash
# Phase 1: Python pre-filter (N came from Stage 3.5 or the preset — don't re-ask)
python scripts/run_matcher.py --mode deep --profile {run_dir}/profile.json --top <N> --output-dir {run_dir}

# Phase 2: shard, then dispatch one subagent per shard. The script prints the dispatch prompts.
# NEVER read shard_*.md files in the main context — they contain full JDs (~190 lines each).
# The sub-agents read them in their own contexts; you only wait for the barrier.
python scripts/shard_deep_candidates.py {run_dir} --per-shard 4
python scripts/check_artifacts.py {run_dir} --kinds deep_shards --wait 360   # timeout: 380000

# Phase 3: collect shards, merge rule scores (40%) + Claude scores (60%), reclassify, regenerate report
python scripts/run_matcher.py --mode deep --merge --output-dir {run_dir}
```

Then open the report: `Invoke-Item {run_dir}\matching_report.html` (PowerShell) or `start {run_dir}/matching_report.html` (Bash).

**For Stage 7a, use `read_thin.py` to get the job table — never `Read` the full `qualified_jobs.json`:**

```bash
python scripts/read_thin.py {run_dir}/qualified_jobs.json --kind jobs
```

This gives you the link, company, position, salary, score, match reasons, and missing items — everything
you need to display the table and run the 7bc gate. The full JDs and company descriptions stay on disk.

### Stage 7: Confirm & Apply

Two user gates — 7bc picks the jobs and the material preferences, 7g approves what was generated —
with a parallel per-job generation phase in between. See
[references/auto-apply.md](references/auto-apply.md) for the flowchart, subagent contracts, the ShowCV
rendering pipeline, and the directory layout.

| Sub-step | Action |
|---|---|
| **7a** | Display recommended jobs table with match scores and reasons |
| **7bc** | `AskUserQuestion` — **gate 1**, all three independent questions in **one** call: 投递范围 (which jobs), 招呼语生成方式 (自定义 / 默认 / AI生成), 是否发送图片 (自定义上传 / AI调整 / 不发送) |
| **7d** | Launch subagents **in parallel, one pair per confirmed job — every `Agent` call in ONE message, or they run serially**. Neither subagent touches a browser. Greeting agents get `scripts/prompts/greeting.st` (via `prompts.get_greeting_prompt()`) — don't re-state its rules in the dispatch prompt |
| **7e** | Render adjusted resumes to flat images — **serial, one batch covering all jobs** (ShowCV) |
| **7f** | Write `{run_dir}/applications/{company}-{position}/` per job via `python scripts/write_application_md.py "{run_dir}" --all` (all 25 crawled fields + greeting — never hand-write it), then notify the user to review |
| **7g** | `AskUserQuestion` — **gate 2**: 全部投递 / 返回修改 / 取消投递 |
| **7h** | On 全部投递, execute `auto_apply_jobs()` with the confirmed greetings and attachments |

**7bc is one call, not three.** Which jobs, greeting method, and image method are mutually
independent — the job selection does not change the greeting options and vice versa. They used to be
two stops (7b then 7c); merging them removes a round-trip from the stage that cost 12 minutes of the
2026-08-14 run. The follow-ups that *depend* on an answer stay where they are: validating a
`自定义上传` path, and the `AI调整` base-resume override.

**7d → 7e → 7f is one uninterrupted batch.** Once 7bc's answers are in, generate, render, and
archive without stopping to ask anything — the two gates are 7bc and 7g, and a question in between
turns material preparation into an interrogation. Report once at the end of 7f.

**The first 15 characters of a greeting are the only part most HR ever see.** BOSS's message-list
preview truncates there, so an opening of `您好，我是…` spends the whole window on nothing. The rules
and the per-scenario formulas live in `scripts/prompts/greeting.st` (one copy, don't paraphrase);
`auto_apply.has_wasted_preview(text)` is the one-line check to run on each greeting before 7f,
including a `自定义` text the user typed — if it fails, say so and offer to re-front-load it, but
**don't rewrite a user-supplied greeting silently.**

**7g is not mergeable and not presetable.** It is the last thing before an irreversible outward-facing
action, and it must see the materials that actually landed on disk — so it cannot move earlier, and no
saved preference may stand in for it.

**A job whose material fails is dropped from the batch, not retried forever.** If a resume agent
produces no artifact, mark that job failed, exclude it from the 7e render batch and from 7h, and list
it for the user at 7g. Judge this by **whether the artifact is on disk** — never by whether a
completion notification arrived. Missing notifications are common here (6 agents dispatched, 3
notifications received), and a slow agent looks identical to a dead one from the inside.

**Which subagents actually launch** in 7d — the non-AI options resolve inline and need no agent:

| 7bc answer | 7d subagent | 7h attachment |
|---|---|---|
| 招呼语 **自定义** | none — one user-supplied text reused for every job | — |
| 招呼语 **默认** | none — `generate_greeting()` per job | — |
| 招呼语 **AI生成** | one greeting agent per job | — |
| 图片 **自定义上传** | none — validate the path, copy into the job dir | the user's image |
| 图片 **AI调整** | one resume agent per job, base = the original resume | the rendered flat PNG |
| 图片 **不发送** | one resume agent per job, base = generic resume structure | none (the PNG is archived only) |

**7e is a barrier**: every resume agent must finish first, because a single
import → export → delete batch covers all jobs. Never run two ShowCV commands concurrently — they
share one browser on debug port 9333, and that port may be an *adopted* browser holding the user's
real resumes (see [references/resume-editor.md](references/resume-editor.md)).


## Package Reference

See [references/scripts.md](references/scripts.md) for module tables, key functions, and import examples for both `boss_crawler/` and `resume_matcher/` packages, and [references/resume-editor.md](references/resume-editor.md) for `showcv/`.

## Key Principles

1. **Claude IS the LLM**: all AI analysis (resume parsing, job matching, optimization) uses Claude's own capabilities — no external LLM API
2. **Rule-first, then LLM**: Python rule-based scoring pre-filters; Claude deep-analyzes only top candidates
3. **Never fabricate**: resume optimization must not invent experience or skills
4. **Safe applying**: 3-5s between applications, max 10-20 per session, pause on captcha
5. **Always visualize**: generate and open HTML report for every run
