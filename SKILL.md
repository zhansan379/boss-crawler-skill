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

> **Confirmation rule**: for paths A/B/C, Stage 7 must show the recommended job list with match reasons BEFORE applying, and must pass **two** user gates: 7b (which jobs) and 7g (approve the generated materials). Never apply without both.

## Run Directory

Every skill invocation creates a timestamped run directory under `assets/` (e.g., `assets/2026-08-10_14-30-00/`). All outputs go there. Within one conversation, reuse the same directory via `--output-dir`.

Path D is the exception — it produces no run outputs, so it needs no run directory.

---

## Workflow

Copy this checklist and check off items as you complete them:

```
Progress:
- [ ] Stage 0: Launch resume editor (path D — terminal step)
- [ ] Stage 1: Crawl jobs (paths A, C)
- [ ] Stage 2-3: Read resume file → parse → profile.json
- [ ] Stage 3.5b: Cross-validate profile (run the script, usually one line)
- [ ] Stage 3.5: Infer crawl params from resume (path C only)
- [ ] Stage 4-6: Match analysis (quick or deep) → report written and opened
- [ ] Stage 7: Confirm jobs → parallel per-job material generation → confirm → apply
```

**Lost your place (e.g. after a context compaction)? Do not re-read the reference docs to rebuild
state.** Ask the filesystem instead:

```bash
python scripts/where_am_i.py           # or pass an explicit <run_dir>
```

It infers the current stage from the run directory's artifacts and prints the next commands in
~500 characters. `references/auto-apply.md` is 25k characters; in the 2026-08-13 run it was read
five times, twice purely because compaction had discarded the earlier read. Consult a reference doc
only for the *one* section `where_am_i.py` points you at, and read that section, not the whole file.

**Two habits that keep a run cheap.** The 2026-08-13 run took 46 minutes to deliver a single
application; 65% of that was model inference across 97 round trips. Both rules below exist to keep
bulk text out of *your* context, because that is what triggers compaction:

1. **Fan bulk analysis out to subagents; keep only the verdicts.** Deep-mode Phase 2 is sharded for
   this reason (`scripts/shard_deep_candidates.py`) — N full JDs never enter the main context. When
   you dispatch agents, require a file write and a one-line reply; never let an agent echo its
   payload back.
2. **Never `Read` a rendered resume image.** Use `scripts/verify_image.py`. One 0.5 MB PNG cost
   638,960 input tokens — 79% of that session's fresh input, in a single tool call.

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

Key flags: `-m custom` (keyword search, recommended), `-d` (include detail pages — critical for matching quality), `-y` (skip confirmation prompt). Login state persists via `assets/chrome_user_data/`.

### Stage 2-3: Read & Parse Resume

Reading the file and structuring it are one pass, not two stages. Ask for the resume file path, then:

```python
from resume_matcher import parse_resume_file, create_run_dir

resume_text = parse_resume_file(file_path)   # PDF / Word / md / markdown / txt
run_dir = create_run_dir()
# write resume_text.txt into run_dir
```

Then read `scripts/prompts/resume_parse.st`, fill in the resume text, and save the structured output
to `{run_dir}/profile.json`. See
[references/resume-parsing.md](references/resume-parsing.md) for the complete JSON schema and
extraction rules.

### Stage 3.5b: Cross-Validate Profile

Always run it; it is cheap and almost always ends in one line. See
[references/resume-parsing.md](references/resume-parsing.md) for the full procedure.

1. Run `python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json`
2. Claude scans the original resume against the profile for anything the dictionary can't see
   (projects, experience bullets, awards, the skills paragraph)
3. Fix whatever either step found, update profile.json
4. Report in one line and move on. Only spend an `AskUserQuestion` when a correction was
   *judgement*, not transcription — a skill you added that the resume only implies, a project you
   merged or split. Exit code 0 plus a clean scan → one line, no question

**Do not replace step 1 with self-review.** `validate_profile.py`'s skill check is a dictionary
lookup (`KNOWN_TECH_TERMS`), so it is the one signal here that is *independent of the model that
produced the JSON*. The failure it catches is "the parse dropped a skill" — and the model that
dropped it is the least likely to notice. Step 2 complements the script, it doesn't substitute for it.

**Exit code 1 means a skill is missing — that alone.** Anything the script prints under `hints`
(unmatched project names, unmatched company names, thin skill categories) comes from loose regex and
exact set-difference, so a wording difference between resume and profile is enough to trigger it.
Read hints, don't obey them, and don't let one turn into a gate.

### Stage 3.5: Infer Crawl Params (path C only)

Claude maps resume fields to crawl parameters. See [references/resume-parsing.md](references/resume-parsing.md) for the inference mapping table.

**Budget keywords by city, not by imagination.** Crawl time is linear in keyword count: 5 keywords
in 太原 returned 117 rows holding 53 unique jobs. Small market → 2-3 keywords; 一线/新一线 → up to 5.
And spend them on distinct concepts — `AI应用开发` and `大模型应用开发` are the same search.

Confirm with **one** `AskUserQuestion` whose first option is the full inferred parameter set, labelled
recommended — so accepting is a single click, and correcting is still one step away. Then run the
Stage 1 crawl with the confirmed parameters.

**Keep this gate even when the inference looks unambiguous.** The crawl is an outward-facing action
driven through the user's own logged-in browser: wrong parameters cost a long crawl plus a batch of
useless data, and there is nothing to undo afterwards. The ambiguity is rarely just "the resume lists
two cities" — expected salary, seniority, and which keyword to search (`Python` vs `后端开发`) are all
judgement calls. What gets collapsed here is a round of typing, not the confirmation itself.

### Stage 4-6: Match Analysis & Report

`run_matcher.py` does all three in one process — loads the CSVs, scores and classifies, then writes
`matching_report.html` and `scored_jobs.json` itself.

**Do not call `generate_html_report()` yourself.** It already runs inside the script, and after a CLI
run you don't hold the `classification` object it needs anyway.

Ask the user to choose a mode. See [references/matching.md](references/matching.md) for mode details, scoring dimensions, and classification logic.

**Quick mode** — single command, rule-based 6-dimension scoring (0-115 pts), zero token cost:
```bash
python scripts/run_matcher.py --mode quick --profile {run_dir}/profile.json --output-dir {run_dir}
```

**Deep mode** — three phases, rule pre-filter + per-job LLM semantic analysis:
```bash
# Phase 1: Python pre-filter (ask user for top-N first)
python scripts/run_matcher.py --mode deep --profile {run_dir}/profile.json --top <N> --output-dir {run_dir}

# Phase 2: shard, then dispatch one subagent per shard (never analyze in the main context —
# N full JDs there is what forced a 167 s compaction). The script prints the dispatch prompts.
python scripts/shard_deep_candidates.py {run_dir} --per-shard 4
python scripts/check_artifacts.py {run_dir} --kinds deep_shards --wait 360   # timeout: 380000

# Phase 3: collect shards, merge rule scores (40%) + Claude scores (60%), reclassify, regenerate report
python scripts/run_matcher.py --mode deep --merge --output-dir {run_dir}
```

Then open the report: `Invoke-Item {run_dir}\matching_report.html` (PowerShell) or `start {run_dir}/matching_report.html` (Bash).

### Stage 7: Confirm & Apply

Two user gates — 7b picks the jobs, 7g approves the generated materials — with a parallel per-job
generation phase in between. See [references/auto-apply.md](references/auto-apply.md) for the
flowchart, subagent contracts, the ShowCV rendering pipeline, and the directory layout.

| Sub-step | Action |
|---|---|
| **7a** | Display recommended jobs table with match scores and reasons |
| **7b** | `AskUserQuestion` — **gate 1**: which jobs to apply to |
| **7c** | `AskUserQuestion` — both independent questions in **one** call: 招呼语生成方式 (自定义 / 默认 / AI生成) and 是否发送图片 (自定义上传 / AI调整 / 不发送) |
| **7d** | Launch subagents **in parallel, one pair per confirmed job**. Neither subagent touches a browser |
| **7e** | Render adjusted resumes to flat images — **serial, one batch covering all jobs** (ShowCV) |
| **7f** | Write `{run_dir}/applications/{company}-{position}/` per job via `python scripts/write_application_md.py "{run_dir}" --all` (all 25 crawled fields + greeting — never hand-write it), then notify the user to review |
| **7g** | `AskUserQuestion` — **gate 2**: 全部投递 / 返回修改 / 取消投递 |
| **7h** | On 全部投递, execute `auto_apply_jobs()` with the confirmed greetings and attachments |

**7d → 7e → 7f is one uninterrupted batch.** Once 7c's preferences are in, generate, render, and
archive without stopping to ask anything — the two gates are 7b and 7g, and a question in between
turns material preparation into an interrogation. Report once at the end of 7f.

**A job whose material fails is dropped from the batch, not retried forever.** If a resume agent
produces no artifact, mark that job failed, exclude it from the 7e render batch and from 7h, and list
it for the user at 7g. Judge this by **whether the artifact is on disk** — never by whether a
completion notification arrived. Missing notifications are common here (6 agents dispatched, 3
notifications received), and a slow agent looks identical to a dead one from the inside.

**Which subagents actually launch** in 7d — the non-AI options resolve inline and need no agent:

| 7c answer | 7d subagent | 7h attachment |
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
