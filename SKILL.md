---
name: boss-crawler
description: Crawls BOSS Zhipin job listings via DrissionPage, parses resumes, matches jobs against candidate profiles using rule-based scoring and LLM semantic analysis, generates HTML visualization reports, auto-applies to matching positions, and launches an embedded Markdown resume editor (ShowCV). Use when the user wants to search BOSS Zhipin jobs, upload a resume for job matching, generate a job matching report, auto-apply to positions, optimize a resume for specific job descriptions, or open the resume editor to write/edit a resume ("打开简历编辑器", "启动 ShowCV", "写一份简历", "预览简历").
---

# BOSS Zhipin Job Crawling & Matching

> **⚠️ 语言强制要求（最高优先级）**：本 skill 面向中文用户。所有对用户的输出——包括提问（AskUserQuestion 的 question/header/option 文案）、说明、进度、报告、确认、错误提示——**一律使用简体中文**。术语、命令、脚本名、文件路径、代码片段可保留英文；除此之外的用户可见文本必须为中文。与用户的任何对话都不允许用英文正文。

One pipeline, eight stages, one driver. Every stage is a command — you run it, read the last few
lines, and decide whether to continue. **All model inference happens inside those commands**, against
the user's own OpenAI-compatible endpoint; you never fill a prompt template yourself and never
dispatch a subagent to do inference.

```
parse → infer → crawl → match → deep → merge → materials → render        [ apply ]
```

`scripts/pipeline.py` is the driver. The full flag table, per-stage scripts, exit-code conventions
and troubleshooting live in **[references/cli.md](references/cli.md)** — read the one section you need, not the
file. All scripts live under `scripts/` relative to this skill directory.

**Drive one stage at a time.** `pipeline.py --from <stage>` runs exactly that stage (`match` also
pulls `deep`+`merge`, since stopping at `match` leaves a half product nothing downstream can read),
then stops and prints the next command. **Never use `--all`**: the gates below sit between stages,
and `--all` runs straight through them, spending the user's tokens on a job list they have not seen.

## Precondition: LLM configuration

Every inference stage needs an API key. Check once, before anything else, and never later than the
first `parse`:

```bash
python scripts/llm_check.py --no-call        # exit 0 = usable, 1 = missing/invalid config
```

`--no-call` costs nothing. Exit 1 → show the user the three ways to configure it (the script prints
them) and stop; do not start a crawl that will die at `match`. Add `--stage deep` to see what a
single stage resolves to, or drop `--no-call` to also send one minimal request. **Never print, log,
or echo the `api_key`** — the scripts mask it, so pass paths and stage names around, not the key.

## Path Selection

**Always ask the user to choose a path at the start of every invocation** — one `AskUserQuestion`
with **exactly 4 options**. Do not auto-select from a saved preset or from whatever happens to be on
disk; a preset supplies parameters *after* a path is chosen.

| Option label | When | Flow |
|------|------|------|
| **A: 简历驱动** ✨ | Have a resume, want precision | parse → infer → crawl → match… → apply |
| **B: 已有岗位数据** | Have a resume and CSVs already in `assets/post_data/` | parse → infer → *(skip crawl)* → match… → apply |
| **C: 预设重放** | Re-run with the saved preset, no re-declaring | preset → parse → infer *(preset values)* → crawl → match… |
| **D: 仅编辑简历** | No resume file yet, wants to write or edit one | Launch resume editor → **stop there** |

**Every path starts at `parse`.** `infer` reads `profile.json`, and `crawl` reads the
`crawl_params.json` that `infer` writes — so "crawl first, parse later" is not a supported order.
Path B differs from A by one thing: skip the `crawl` stage (`--from match` after `infer`).

**AskUserQuestion option cap (every question in this skill):** at most 4 options per question. When a
choice has more candidates — salary brackets, keyword picks — take the 4 most relevant with a
recommended default first and let the auto-added 「其他」 carry the rest. Never emit a 5th option; the
tool rejects the call. Split an over-long question into a follow-up rather than failing the call.

**Path A is recommended**: the resume tells you what to search — skills, city, salary range — so
crawled jobs align with the candidate's background instead of with guessed keywords.

**Path D terminates at launch.** It opens the editor and reports the URL, nothing else. It commonly
serves as a precursor: the user writes a resume, then re-enters at A/B/C. The editor's
stored Markdown can feed `parse` directly (see
[references/resume-editor.md](references/resume-editor.md)) — but only when the user asks.

**Path C is the preset path.** `preferences.py show` prints the saved params, `preferences.py missing`
names any askable field the preset lacks (薪资/规模/最低岗位数 and siblings). Ask about **exactly
those**, merge them back with `preferences.py save`, then pass the whole set to `infer` as flags. If
`show` exits 1 there is no preset — fall back to path A's fresh confirmation instead of erroring.

> **Two gates, both mandatory.** `gate:jobs` (which jobs, and how materials are made) and
> `gate:send` (approve what actually landed on disk). Never apply without both.
>
> **Presets never reach these gates.** `assets/preferences.json` covers crawl and matching
> parameters only — it has no field for which jobs, what greeting, or whether to send, and `load()`
> drops any key outside its whitelist.

## Run Directory

`parse` creates a timestamped run directory under `assets/` (e.g. `assets/2026-08-16_14-30-00/`) and
points `assets/LATEST.txt` at it. Every later stage finds it automatically; pass `--run-dir` to be
explicit. Path D produces no run outputs and needs no run directory.

---

## Workflow

Copy this checklist and check off items as you complete them:

```
Progress:
- [ ] llm_check.py --no-call        (precondition — exit 1 stops the run)
- [ ] Path selection: one AskUserQuestion, 4 options (A / B / C / D)
- [ ] Stage 0: launch resume editor (path D — terminal step)
- [ ] parse:     resume file → profile.json
- [ ] infer:     confirm params (2 batched questions + min_count) → crawl_params.json
- [ ] crawl:     background run, then check the floor (paths A, C)
- [ ] match:     → deep → merge → matching_report.html + qualified_jobs.json
- [ ] gate:jobs  one AskUserQuestion: which jobs + greeting method + image method
- [ ] materials: greetings + optimized resumes
- [ ] render:    resume long-images (skip with --no-images)
- [ ] write_application_md.py --all, then verify_image.py
- [ ] gate:send  one AskUserQuestion → apply.py --yes
```

**Four stops.** Path selection, the `infer` confirmation (two batched `AskUserQuestion` calls plus a
small `min_count` follow-up — skipped when path C reuses a complete preset), `gate:jobs`, and
`gate:send`. Plus one conditional stop: the crawl floor, only when the pool came in thin.

**Lost your place (e.g. after a context compaction)? Do not re-read docs to rebuild state.** Ask the
filesystem:

```bash
python scripts/where_am_i.py           # or pass an explicit <run_dir>
```

It infers the stage from artifacts on disk and prints the next command in ~1k characters. Consult a
reference doc only for the *one* section it points you at.

**Three habits that keep a run cheap.**

1. **Never `Read` a rendered resume image.** Use `scripts/verify_image.py`. One 0.5 MB PNG cost
   638,960 input tokens — 79% of that session's fresh input, in a single tool call.
2. **Never `Read` full data files — use `read_thin.py`.** `qualified_jobs.json` carries full JDs and
   company descriptions; you only need link/company/position/score/verdicts:

   ```bash
   python scripts/read_thin.py {run_dir}/qualified_jobs.json --kind jobs     # → table fields
   python scripts/read_thin.py {run_dir}/profile.json --kind profile         # → summary stats
   python scripts/read_thin.py {run_dir}/deep_results.json --kind deep       # → verdicts only
   ```

3. **Run the long stages in the background and grep the output.** `crawl` takes tens of minutes and
   `deep`/`materials` print one line per job — piping all of it through your context is what triggers
   compaction. Start them with `run_in_background`, then read only what matters:

   ```bash
   grep -E "✅|❌|⚠|阶段|失败|写入" <background task output file> | tail -20
   ```

Timings land in `{run_dir}/run_timings.jsonl` automatically — every stage instruments itself, so
there is nothing to mark by hand. `python scripts/stage_timer.py report <run_dir>` ranks them.

### Stage 0: Launch Resume Editor (path D)

Serves the embedded ShowCV build (`app/`) locally and opens it in an isolated Chromium. No
`pnpm install` or node needed. See [references/resume-editor.md](references/resume-editor.md) for
design rationale, limitations, and the `storage.py` data-moving tool.

**Step 1 — start the static server** (background task):

```bash
python scripts/showcv/serve.py
```

Wait for the ready signal and read the actual address from it:

```bash
until grep -q "SHOWCV_READY" "<background task output file>"; do sleep 0.3; done
grep "SHOWCV_READY" "<background task output file>"
```

First line is always `SHOWCV_READY http://127.0.0.1:<port>`, default 3090. **If that background
process exits immediately but still printed `SHOWCV_READY`**: the service was already running and
this run reused it. Use the address and continue — do NOT restart it or pick another port; the port
is what scopes the user's saved resumes.

**Step 2 — open the browser** (use the address from step 1, don't assume 3090):

```bash
python scripts/showcv/launch.py http://127.0.0.1:3090
```

Prints `url=` / `title=` / `profile=` on success, with `ShowCV` in the title. **If the title lacks
`ShowCV` the script exits 1** — the build is incomplete or the server isn't up. Don't report success.
Optional flags: `--headless`, `--close`, `--browser <exe>`.

**Step 3 — report to the user**: the URL, that the browser is open, and **how to stop it** —
`TaskStop` on the step-1 background task; they close the browser window themselves.

Then stop. Path D ends here.

### Stages 0.5 / 0.6 / 0.7: ShowCV standalone tools (on request only)

**Deliberately not wired into any path and not in the Progress checklist.** All three assume Stage 0
already ran (server up, browser open) and fail rather than starting it themselves. Read the URL from
Stage 0's `SHOWCV_READY` line — `--url` has no default on purpose.

```bash
# 0.5 batch-import Markdown into the editor's resume list
python scripts/showcv/import_md.py --url http://127.0.0.1:3090 <files-or-dirs> [-r] [--dry-run]

# 0.6 export resumes as images (repeatable --id, or --all; one call covers a batch)
python scripts/showcv/export_images.py --url http://127.0.0.1:3090 [--name N | --id I | --all] \
    [--mode paginated|flat] [--scale 1|2|3] [--out DIR] [--dry-run]

# 0.7 delete resumes — destructive, localStorage is the only copy
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --dry-run
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --yes
```

`export_images.py` resolves names to ids locally, so a typo fails before anything is exported, and it
confirms files reached disk rather than trusting the page's "已下载" text. `delete_resumes.py` without
`--yes` only prints the plan; with `--yes` it backs up first (restore command printed), goes through
the site's own confirmation page, and aborts if the names there differ from what it resolved. Unlike
`/export`, a missing `id` is never taken to mean "the current resume".

### parse — resume file → profile.json

Ask for the resume file path, then one command:

```bash
python scripts/pipeline.py "简历.pdf"
```

PDF / Word / md / markdown / txt. It writes `resume_text.txt`, `profile.json` and
`profile_validation.json` into a fresh run directory. **Don't read the resume to "check" a clean
parse** — the validator already did, and the text costs context for no new information. Read
`resume_text.txt` only when the validator exits 1, a hint looks like a real omission, or the user asks
for a thorough check ([references/resume-parsing.md](references/resume-parsing.md)).

`profile_validation.json` is that validator's output. Exit code 1 from the parse stage means a known
tech term appears in the resume but not in the profile — that is a dictionary lookup
(`KNOWN_TECH_TERMS`), the one signal here independent of the model that produced the JSON. Anything
it prints under `hints` (unmatched project/company names, thin skill categories) comes from loose
regex: read hints, don't obey them, and don't let one become a gate. To re-run it alone:

```bash
python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json
```

Use `read_thin.py --kind profile` if you need to confirm a specific field. See
[references/resume-parsing.md](references/resume-parsing.md) for the schema.

### infer — profile.json → crawl_params.json

`crawl_params.json` is **mandatory**, not an optimization: `crawl` builds its argv from it, and
`match` reads `match_mode`/`top_n` out of it. Skipping this stage means the crawl cannot start and the
match silently falls back to quick mode.

**Budget keywords by city, not by imagination.** Crawl time is linear in keyword count: 5 keywords in
太原 returned 117 rows holding 53 unique jobs. Small market → 2-3 keywords; 一线/新一线 → up to 5.
Spend them on distinct concepts — `AI应用开发` and `大模型应用开发` are the same search.

Confirm in **two** `AskUserQuestion` calls plus one small follow-up (4 questions max per call):

1. **爬取与匹配核心** — city, keywords (multi-select), match mode (quick/deep), deep's Top-N.
2. **列表筛选** — experience, job type, salary floor, company scale. First option in each is the
   candidate-appropriate default, so accepting is one click; leave one empty to skip that filter.
3. **最低岗位数量 (`min_count`)** — the floor below which a crawl counts as thin. Default ~10, 0
   disables the check. Separate because it is a sufficiency threshold, not a list filter.

Then pass every confirmed value as a flag. Fully-specified parameters mean the stage makes **no model
call at all** — it only calls the model for the fields you leave out:

```bash
python scripts/pipeline.py --from infer --city 太原 --keywords "AI应用开发,Python" \
    --match-mode deep --top-n 10 --count 20 --degree 本科 \
    --experience 应届生 --job-type 实习,全职 --salary 5-10K --min-count 10
```

Accepted filter values are the Chinese labels in `boss_crawler/config.py` (`EXPERIENCE_MAP` /
`JOB_TYPE_MAP` / `SALARY_MAP` / `SCALE_MAP`). There is **no `校招` job type** (only 全职/实习/兼职);
unknown values are warned about and skipped, and `不限` is skipped too — prefer omitting the flag.
`--scale` is never inferred, only given.

Then save the answers so the next run can replay them (path C):

```bash
python scripts/preferences.py save --city 太原 --keywords "AI应用开发,Python" \
    --match-mode deep --top 10 --count 20 --degree 本科 \
    --experience 应届生 --job-type 实习,全职 --salary 5-10K --min-count 10
```

**Keep this gate even when the inference looks unambiguous.** The crawl is an outward-facing action
driven through the user's own logged-in browser: wrong parameters cost a long crawl plus a batch of
useless data, and there is nothing to undo. What gets collapsed here is rounds of typing, not the
confirmation.

### crawl — → assets/post_data/**.csv (paths A, C)

Login first. This one is interactive by nature, so run it in the foreground:

```bash
python scripts/boss_post_interactive.py --ensure-login
```

`[LOGIN_OK]` → browser closes, continue. `[LOGIN_NEEDED]` → browser stays open, the user logs in and
tells you 已登录, then re-run. Login state persists in `assets/chrome_user_data/`.

Then crawl — **background task, tens of minutes**, argv built from `crawl_params.json`:

```bash
python scripts/pipeline.py --from crawl
```

The floor check runs afterwards on its own: the threshold comes from `min_count` in
`crawl_params.json`, and `--min-jobs N` only overrides it. A missing `crawl_summary.json` means
**nothing was crawled** —
the crawler exits 0 when it detects a logged-out session, so exit code alone cannot tell you. When
the floor trips, **stop and ask the user** — 换关键词 / 放宽筛选 / 接受现状（继续，`--min-jobs 0`）—
rather than proceeding on a thin pool. A small-city crawl can legitimately end early; that is the
case this check exists to surface, not to override.

Row count is an upper bound on jobs and it counts **the whole `assets/post_data/` pool**, not just
this run: one job matching three keywords is written three times and deduped at load. See
[references/crawl-commands.md](references/crawl-commands.md) for the full parameter table.

### match → deep → merge — scoring and the report

One command covers all three; `deep`/`merge` no-op in quick mode:

```bash
python scripts/pipeline.py --from match          # deep mode: background task, one request per job
```

- **quick** — rule-based 6-dimension scoring (0-115 pts), seconds, zero token cost.
- **deep** — rule pre-filter to Top-N, then one model request per candidate, then a merge that blends
  rule score (40%) with model score (60%), reclassifies, and regenerates the report.

Both write `matching_report.html` and `qualified_jobs.json` (the apply pool = 符合 + 需优化, in raw
crawled fields). **Never hand-write `qualified_jobs.json`.** Don't call `generate_html_report()`
yourself either — the script already did, and after a CLI run you don't hold the object it needs.

Open the report for the user: `Invoke-Item {run_dir}\matching_report.html` (PowerShell) or
`start {run_dir}/matching_report.html` (Bash). See [references/matching.md](references/matching.md)
for scoring dimensions and classification logic.

### gate:jobs — one question, three axes

Show the table first (`read_thin.py --kind jobs` — never `Read` the file), then **one**
`AskUserQuestion` covering three mutually independent choices:

| Axis | Options |
|---|---|
| 投递范围 | which jobs (job selection doesn't change the other two) |
| 招呼语生成方式 | 自定义 / 默认模板 / AI生成 |
| 是否发送图片 | 自定义上传 / AI调整（渲染长图） / 不发送 |

Map the answers onto flags rather than post-editing files:

| Answer | How it is executed |
|---|---|
| 投递范围 = a subset | `--only 1,3,5-7` on `materials` **and** `render` (1-based index into `qualified_jobs.json`) — don't rewrite the file |
| 招呼语 **自定义** | write the text to `{run_dir}/generated/greeting_{i}_custom.txt` for each chosen `i`, then run materials with `--greeting-mode skip`… or leave the mode at `ai`: an existing non-empty artifact is skipped, never overwritten |
| 招呼语 **默认模板** | `--greeting-mode default` (rule template, no model call) |
| 招呼语 **AI生成** | `--greeting-mode ai` (default) |
| 图片 **自定义上传** | validate the path, `--resume-mode skip`, then `apply.py --image <path>` at send time. `skip` also suppresses `render` — the generated PNG would never be sent ([auto-apply.md](references/auto-apply.md)) |
| 图片 **AI调整** | default: `materials` writes the resume JSON, `render` turns it into the long image |
| 图片 **不发送** | `pipeline.py --no-images`, and `apply.py --no-image` at send time |

**The first 15 characters of a greeting are the only part most HR ever see.** BOSS's message-list
preview truncates there, so `您好，我是…` spends the whole window on nothing. The rules and
per-scenario formulas live in `scripts/prompts/greeting.st` — one copy, don't paraphrase. Run
`auto_apply.has_wasted_preview(text)` on each greeting before sending, **including a 自定义 text the
user typed**: if it fails, say so and offer to re-front-load it, but **never silently rewrite a
user-supplied greeting.**

### materials — greetings + optimized resumes

```bash
python scripts/pipeline.py --from materials --only 1,3,5     # background task
```

Two model requests per job (greeting + resume rewrite), so this is the stage that spends money on the
list the user just approved — which is exactly why `gate:jobs` comes first. Products are
`generated/greeting_{i}_{company}.txt` and `generated/resume_{i}_{company}.json`, where `{i}` is the
1-based index in `qualified_jobs.json`. **The index is the alignment key for everything downstream**,
so a failed job leaves a gap rather than shifting the others.

Partial success exits 3, not 1. Check and top up only what is missing — existing non-empty artifacts
are skipped, never overwritten (`--force` to overwrite):

```bash
python scripts/check_artifacts.py {run_dir}
python scripts/gen_materials.py {run_dir} --only 4        # just the one that failed
```

**A job whose material fails is dropped from the batch, not retried forever.** Exclude it from
`render` and from the apply list, and tell the user at `gate:send`.

### render — resume long-images

```bash
python scripts/pipeline.py --from render --only 1,3,5
```

**Serial by design — there is no `--workers`.** All resumes share one browser, one origin and one
`localStorage`; concurrency only makes them trample each other. The script refuses to run when debug
port 9333 is held by a browser whose `--user-data-dir` isn't `assets/showcv_profile` — that port may
be an *adopted* browser holding the user's real resumes (see
[references/resume-editor.md](references/resume-editor.md)). Never pass `--adopt-browser` on the
user's behalf.

Skip the stage entirely with `--no-images` when the user chose 不发送.

### Materials on disk, then a look

```bash
python scripts/write_application_md.py "{run_dir}" --all
python scripts/verify_image.py "{run_dir}/applications" --all
```

The first writes `applications/{company}-{position}/岗位信息+招呼语.md` per job — all crawled fields
plus the greeting. Never hand-write it. The second is how you check the images: it returns a dozen
lines of numbers instead of a 639k-token screenshot. If the user wants to see one, give them the
path and let them open it.

### gate:send — approve, then apply

One `AskUserQuestion`: 全部投递 / 返回修改 / 取消投递. Then, and only then:

```bash
python scripts/apply.py "{run_dir}"                # dry run: prints the list, touches no browser
python scripts/apply.py "{run_dir}" --yes          # sends
```

**`--yes` is the only irreversible step in this skill** — a sent message arrives instantly and cannot
be recalled. Always dry-run first and show that list. `gate:send` is **not mergeable and not
presetable**: it must see the materials that actually landed on disk, so it cannot move earlier, and
no saved preference may stand in for it. `pipeline.py` never runs `apply.py`, not even with `--all`.

Useful flags: `--only 1,3,5`, `--company 百度,棱镜数聚`, `--max N`, `--image <path>`, `--no-image`,
`--name 张三`. Results land in `{run_dir}/apply_log.json`. Three checks refuse to send rather than
warn — missing greeting, missing/blank attachment image, unreadable run directory. See
[references/auto-apply.md](references/auto-apply.md) for the directory layout and the
send-then-verify readback.

## Package Reference

See [references/scripts.md](references/scripts.md) for module tables and key functions of
`boss_crawler/` and `resume_matcher/`, [references/resume-editor.md](references/resume-editor.md) for
`showcv/`, and [references/cli.md](references/cli.md) for every command's flags and exit codes.

## Key Principles

1. **Rule-first, then LLM**: Python rule scoring pre-filters; the model deep-analyzes only top candidates
2. **Never fabricate**: resume optimization must not invent experience or skills
3. **Safe applying**: 3-5s between applications, max 10-20 per session, pause on captcha
4. **Always visualize**: generate and open the HTML report for every run
5. **Artifacts on disk are the truth**: judge a stage by its files and exit code, never by a notification
