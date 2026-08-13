# Auto-Apply Reference

Stage 7 turns confirmed matches into per-job application materials, then applies. Two user gates
bracket the generation phase: **7b** picks the jobs, **7g** approves what was generated. Never apply
without passing both.

## Stage 7 Flow

```mermaid
flowchart TD
    Start[Stage 7: Confirm & Apply] --> J1[7a 展示推荐岗位表]
    J1 --> J2{7b 门禁一：投递哪些岗位}
    J2 --> |取消| End[结束]
    J2 --> |已选 N 个岗位| Q[7c 单次 AskUserQuestion<br>同时问两个独立问题]

    Q --> Q1{招呼语生成方式}
    Q1 --> |自定义| Opt1[用户输入一段<br>全岗位共用，不启子智能体]
    Q1 --> |默认| Opt2[generate_greeting 模板<br>不启子智能体]
    Q1 --> |AI生成| Opt3[每岗位一个子智能体]

    Q --> Q2{是否发送图片?}
    Q2 --> |是| Q3{图片处理方式}
    Q3 --> |自定义上传| Upload[校验图片存在并复制进目录<br>不启简历子智能体]
    Q3 --> |AI调整| AIAdjust[基底 = 初始上传的简历<br>提示用户，可重新上传替换]
    Q2 --> |否| NoPic[不作为附件发送<br>基底 = 通用简历结构]

    Opt1 & Opt2 & Opt3 --> GreetingReady[招呼语方式确定]
    Upload & AIAdjust & NoPic --> PicReady[图片方式确定]

    GreetingReady --> Fan[7d 准备就绪，按岗位并行]
    PicReady --> Fan

    Fan --> GreetingAgent[子智能体：生成招呼语<br>仅 AI生成 分支]
    Fan --> ResumeAgent[子智能体：调整简历 Markdown<br>非 自定义上传 分支]

    GreetingAgent --> GreetingDone[招呼语就绪]
    ResumeAgent --> ResumeDone[调整后简历 Markdown 就绪]

    ResumeDone --> Render[7e 串行批量渲染平铺图<br>ShowCV: import → export → delete]
    GreetingDone & Render --> CreateDir[7f 建目录<br>run_dir/applications/公司名-岗位名]
    CreateDir --> SaveFiles[写入三个文件<br>1. 岗位信息+招呼语.md<br>2. 姓名-应聘岗位.md<br>3. 姓名-应聘岗位.png]
    SaveFiles --> Notify[通知用户：已生成 N 份<br>请手动浏览确认]
    Notify --> Confirm{7g 门禁二：用户确认}

    Confirm --> |全部投递| AutoApply[7h auto_apply_jobs]
    AutoApply --> End
    Confirm --> |返回修改| Which{改哪些岗位}
    Which --> Q
    Confirm --> |取消投递| End
```

Two shapes in that diagram are load-bearing:

- **7c asks both questions in one `AskUserQuestion` call.** They are independent — greeting method
  does not constrain image method — so asking them separately costs the user an extra round trip
  for nothing. `AskUserQuestion` takes 1–4 questions per call; use two.
- **7e is a barrier, 7d is not.** Resume agents fan out per job with no synchronization, but the
  rendering step waits for all of them, because one import/export/delete batch covers every job.
  **The barrier is settled by checking that the artifact files exist — never by waiting for
  completion notifications.** See 「7d→7e 交接：按产物判定」 below; this distinction has already
  caused one silent stall.

## 7a: Display Recommended Jobs

```
📋 Recommended Jobs (N qualified)

| # | Company | Position | Salary | Match | Difficulty | Reason |
|---|---------|----------|--------|-------|------------|--------|
| 1 | XX科技  | Java开发  | 15-25K | 85%   | 易         | 技能高度匹配 |
| 2 | YY公司  | 全栈工程师 | 20-30K | 78%   | 中         | 技术栈匹配 |
```

Include: match strengths, potential gaps, total application count, and priority order.

## 7b: Gate 1 — Confirm Jobs

`AskUserQuestion`: apply to all qualified, or select specific ones? The selected set drives
everything downstream — one directory, one greeting, and (usually) one adjusted resume per job.

## 7c: Ask Both Questions in One Call

| Question | Options |
|---|---|
| 招呼语生成方式 | `自定义` (user text, shared by all jobs) / `默认` (`generate_greeting()` template) / `AI生成` (subagent per job) |
| 是否发送图片 | `自定义上传` (user's image path) / `AI调整` (render an adjusted resume) / `不发送` |

**`自定义` greeting is one text for every selected job**, supplied via the "Other" field. Per-job
custom text is not offered — that is what `AI生成` plus 7g's 返回修改 is for.

**`自定义上传` needs a valid path**, so validate before fanning out in 7d:

- Verify the file exists (`os.path.exists(path)`)
- Accept `.jpg/.jpeg/.png/.gif/.webp/.bmp`
- If the file is missing, ask again — never silently downgrade to "no attachment"
- Copy it into each job directory so the archive is self-contained

**`AI调整` follow-up.** Tell the user the base will be the resume already parsed in Stage 2
(`{run_dir}/resume_text.txt`) and let them override it. One extra `AskUserQuestion`:

> 📄 调整简历的内容基底
> - ✅ 用初始上传的简历（`{run_dir}/resume_text.txt`）
> - 📤 重新上传（在 "Other" 里给出文件路径）

An overriding path goes through `parse_resume_file()` the same way Stage 2 does. Note this replaces
only the *content base* for resume adjustment — `profile.json` and the match scores still come from
the originally parsed resume, so do not re-run matching.

## 7d: Parallel Per-Job Generation

One pair of subagents per confirmed job, all launched in parallel. **Neither subagent touches a
browser** — they return text, and every browser action happens in the main agent (7e, 7h). This is
what makes unrestricted parallelism safe.

Skip rules (from the table in `SKILL.md`): the greeting agent launches only for `AI生成`; the resume
agent launches for `AI调整` and `不发送`, but not for `自定义上传`.

### Greeting agent contract

Input: the job's row from the match results (company, position, JD, match reasons), plus
`profile.json`. Output: the greeting text only — no commentary.

**The agent must write its output to `{run_dir}/generated/greeting_{i}_{company}.txt` as well as
returning it** (`{i}` is the job's 1-based index in `qualified_jobs.json`). The file is the artifact;
the return value is only a convenience. See 「7d→7e 交接」 for why.

**5-Paragraph Greeting Structure:**

| Paragraph | Content | Source |
|-----------|---------|--------|
| Opening | "您好，我是{name}，对贵公司「{position}」感兴趣" | `profile.basic_info` |
| Education + experience | "毕业于{school}，{degree}（{major}），{years}年经验" | `profile.education` + `profile.experience` |
| Skills match | "熟练掌握{skills}，与岗位要求高度匹配" | `highlight` + JD-overlapping skills |
| Project highlights | "曾主导{project}，具备落地经验" | `optimization_points` project achievements |
| Closing | "希望能有机会进一步沟通，期待您的回复！" | Fixed |

**Rules:**

- Keep under 300 characters (BOSS Zhipin chat limit)
- Only use skills and experience actually in the resume — never fabricate
- Prioritize skills that appear in the JD

### Resume agent contract

Reuse `scripts/prompts/resume_optimize.st` — it already takes the JD plus the match analysis and
returns `optimized_resume` as Markdown under a no-fabrication rule. The agent fills that template,
and 7e renders the `optimized_resume` field. Its `optimization_suggestions` / `key_changes` fields
are useful review context; keep them in the job directory rather than discarding them.

Output: the template's JSON object. `optimized_resume` inside it is the renderable document —
Markdown, no commentary, and not wrapped in a code fence.

**The agent must write that JSON object to `{run_dir}/generated/resume_{i}_{company}.json` as well as
returning it.** Saving the whole object (not just `optimized_resume`) is what keeps
`optimization_suggestions` / `key_changes` available to 7f without a second agent round.

**No personal information in the rendered resume.** The template's 注意事项 #4 (`不需要返回个人信息`)
is deliberate, not an oversight: on BOSS Zhipin the HR already sees the account's name, and the
greeting opens with `我是{name}`, so repeating name / phone / email inside an image that may be
forwarded onward only adds leakage. `optimized_resume` therefore starts at the first content section
(教育背景 / 专业技能 / …) — no name heading, no contact line. Do not override this in the agent
prompt.

| 7c answer | Content base (`resume_text` slot) |
|---|---|
| `AI调整` | the original resume text (`{run_dir}/resume_text.txt`, or the user's override) — keep its section set and ordering, re-weight the wording toward this JD |
| `不发送` | a generic resume structure (基本信息 / 教育背景 / 技能 / 项目经历 / 工作经历), populated from `profile.json` |

`不发送` still runs the agent and still renders a PNG — the image just is not attached in 7h. It is
archived so 7g's manual review shows the same two files for every job.

**Never fabricate.** Both bases carry the template's own constraint: re-order, re-word, and
re-emphasize what the resume already contains. Do not invent employers, dates, skills, or metrics.
An agent that cannot find JD-relevant material must leave the gap visible rather than fill it —
`must_add` exists to tell the *user* what to supply, not to license the model to supply it.

## 7d→7e 交接：按产物判定，不按通知判定

Background agents report completion through a notification, and a notification is a *speedup*, not a
correctness guarantee. It can fail to arrive. When it does, the main agent has no other wake signal
— it is only re-invoked by an event, so no event means no turn, and the run sits idle indefinitely
even though every agent already finished. The user sees a hang with no error.

This is not hypothetical. In the 2026-08-13 mock run, all 6 agents launched, the 3 greeting agents
finished *first* (files on disk at 09:54:01/09:54:15), and only the 3 resume agents ever produced a
notification. The main agent announced "等 3 个招呼语 agent" at 09:57:14 and stalled for two minutes
until the user typed 「你还在执行吗」 — at which point a single directory listing showed all 6
artifacts had been present the whole time.

So before starting 7e, settle the barrier against the filesystem. Both 7d contracts require each
agent to write its artifact to `{run_dir}/generated/`, which is what makes this check possible:

```bash
python scripts/check_artifacts.py "{run_dir}"
```

Expect one greeting and one resume per job (minus whatever the 7c skip rules exempt). Then:

- **All present** → proceed to 7e immediately, regardless of which notifications arrived.
- **Some missing** → re-dispatch only the missing ones. Do not re-run agents whose artifact exists;
  that burns tokens and can overwrite good output with a worse sample.
- **Still missing after one re-dispatch** → **drop that job from the batch.** Mark it failed, leave it
  out of the 7e render batch and out of the 7h apply list, and surface it in the 7g summary
  ("N 个岗位材料生成失败，已跳过"). One retry, then move on — a stuck agent must not hold up the
  other jobs' materials.
- **Nothing arrived after a couple of minutes** → check the filesystem *before* concluding anything
  is still running. A missing notification and a slow agent look identical from the inside.

The same rule applies to any fan-out in this skill: **an agent's artifact on disk is the source of
truth for whether it finished.** Track expected artifacts explicitly rather than counting
notifications.

## 7e: Batch-Render Flat Images (serial)

Runs once, after every resume agent's artifact is confirmed on disk (see above). Skip entirely when
the 7c answer was `自定义上传`.

```bash
# 1. ensure ShowCV is up — Stage 0 steps 1–2. Reuse a running instance; do not pick a new port
python scripts/showcv/serve.py                  # background; read the SHOWCV_READY line
python scripts/showcv/launch.py <url-from-step-1>

# 2. one import for all jobs (import_md.py batches at 50 automatically)
python scripts/showcv/import_md.py --url <url> {run_dir}/showcv_staging/*.md

# 3. one export, flat mode, addressed by the staged names
python scripts/showcv/export_images.py --url <url> --mode flat \
    --out {run_dir}/showcv_exports \
    --name "XX科技-Java开发__20260813-1430" --name "YY公司-全栈工程师__20260813-1430"

# 4. distribute the PNGs into the job dirs (renaming to <姓名>-<应聘岗位>.png), then remove the
#    temp resumes
python scripts/showcv/delete_resumes.py --url <url> --yes \
    --name "XX科技-Java开发__20260813-1430" --name "YY公司-全栈工程师__20260813-1430"
```

**Stage the Markdown under unique filenames first.** `import_md.py` takes each resume's name from
the filename minus extension, and duplicates become `名字 (2)`, `(3)` … — which silently breaks the
name → id resolution that steps 3 and 4 depend on. So write staging copies as
`{run_dir}/showcv_staging/<company>-<position>__<run-stamp>.md`, with the stamp guaranteeing
uniqueness, and sanitize `/ \ : * ? " < > |` out of company and position for both the filename and
the directory name.

**Read the output naming before hunting for missing files**: one resume in `flat` mode downloads as
`<name>.png`; two or more arrive as `showcv-images-<YYYYMMDD>.zip` containing one `<name>.png` per
resume — i.e. named after the *staged* name, stamp and all. Unzip, then rename on the way into the
job directory (below).

### The saved filename is `<姓名>-<应聘岗位>`

Both files that represent the resume itself use one base name, `<姓名>-<应聘岗位>`:

| File | Path |
|---|---|
| Markdown resume | `{run_dir}/applications/<company>-<position>/<姓名>-<应聘岗位>.md` |
| Flat image | `{run_dir}/applications/<company>-<position>/<姓名>-<应聘岗位>.png` |

- **`姓名`** comes from `{run_dir}/profile.json` → `basic_info.name`. If it is empty or `未提取`,
  ask the user for their name with one `AskUserQuestion` before 7e — this string is what the
  recruiter sees on the attachment, so do not guess it and do not fall back to a placeholder.
- **`应聘岗位`** is that job's position title, no company name. Two postings with the same title
  don't collide because they sit in different `<company>-<position>/` directories.
- Sanitize `/ \ : * ? " < > |` out of both halves, same as for the directory name.
- The `.md` is the same Markdown that was staged for ShowCV — copy it in, don't regenerate it, so the
  image and the Markdown can never disagree.

**Staging names stay unique and stamped; the rename happens on distribution.** Keep the
`<company>-<position>__<run-stamp>` staging name for import/export/delete — `<姓名>-<应聘岗位>`
is *not* unique inside ShowCV's global resume list (`姓名` is constant and the stamp is per-run, not
per-job, so two same-title jobs would land on one name and get silently renamed to `名字 (2)`,
breaking name → id resolution). So resolve every ShowCV call by the staged name, then rename to
`<姓名>-<应聘岗位>.png` / `.md` only as you move the files into the job directory.

**Confirm which browser you are driving before step 2.** Debug port 9333 is shared with the upstream
`showcv-launch` skill, and an already-running browser is *adopted* with the configured profile
silently discarded — so an import can land in the user's real resume list and a delete can remove
their resumes. Every one of these scripts prints the PID-resolved profile as its first line; read
it. `delete_resumes.py` takes a full backup first and aborts without clicking if the page's list
differs from what it resolved locally. See
[references/resume-editor.md](references/resume-editor.md) for the full trap.

**Never run two of these commands concurrently.** They drive one browser, and `import_md.py`,
`storage.py` and `delete_resumes.py` all mutate through the *main* tab — a second command mid-flight
would have zustand `persist` write stale in-memory state back over the result.

## 7f: Directory Layout & Files

One directory per job, under the run directory:

```
{run_dir}/
  profile.json
  qualified_jobs.json
  matching_report.html
  generated/                           # 7d agent artifacts — the 7d→7e barrier checks these
      greeting_1_XX科技.txt
      resume_1_XX科技.json
  showcv_staging/                      # temp Markdown for 7e, safe to delete
  showcv_exports/                      # raw ShowCV download (png or zip)
  applications/
      XX科技-Java开发/
        岗位信息+招呼语.md
        张三-Java开发.md
        张三-Java开发.png                # 自定义上传 → the user's image, renamed the same way
      YY公司-全栈工程师/
        岗位信息+招呼语.md
        张三-全栈工程师.md
        张三-全栈工程师.png
```

`张三-Java开发.md` / `.png` are the resume itself, named `<姓名>-<应聘岗位>` — see the naming rule in
7e. Both live in the job directory, so the same 姓名 and a repeated position title never collide.

`岗位信息+招呼语.md` holds the job facts and the greeting together, so one file answers "what did I
send to whom":

```markdown
# XX科技 - Java开发

| 字段 | 值 |
|---|---|
| 公司 | XX科技 |
| 岗位 | Java开发 |
| 薪资 | 15-25K |
| 城市 | 北京 |
| 匹配度 | 85% |
| 链接 | https://www.zhipin.com/job_detail/... |

## 匹配理由
技能高度匹配：Spring Boot / MySQL / Redis 全部命中 JD 要求。

## 招呼语（285 字）
您好，我是冯雷霆，2026 届计算机科学与技术专业本科应届生，
对贵公司的「Java开发」岗位非常感兴趣。
...
希望能有机会进一步沟通，期待您的回复！
```

Also keep the adjusted resume's Markdown source in the job directory when the resume agent ran — the
PNG is not editable, and a later 返回修改 should not have to regenerate from scratch.

## 7g: Gate 2 — Approve

Notify with the directory paths, then one `AskUserQuestion` covering all jobs at once:

```
✅ 已生成 5 份投递材料，请浏览确认：
  {run_dir}/applications/XX科技-Java开发/
  {run_dir}/applications/YY公司-全栈工程师/
  ...
```

| Answer | Next |
|---|---|
| ✅ 全部投递 | proceed to 7h |
| ✏️ 返回修改 | ask which jobs, then re-run 7c → 7f for just those. Untouched jobs keep their materials |
| ❌ 取消投递 | stop. The generated directories stay on disk |

Per-job approval is deliberately not offered: the materials are already on disk for inspection, and
a five-question confirmation loop after a five-job generation reads as an interrogation. 返回修改
covers the "most are fine, one is off" case.

## 7h: Execute Apply

```python
from resume_matcher import auto_apply_jobs

results = auto_apply_jobs(
    qualified_jobs=selected_jobs,
    _profile=profile,
    max_applications=len(selected_jobs),
    greetings=greetings,              # keyed by job link
    resume_file_path=resume_images,   # keyed by job link; None when 7c answered 不发送
    output_dir=run_dir,
)
```

`resume_file_path` takes either a single path (whole batch shares one attachment) or a
`{job_link: path}` dict (per-job attachment) — pass the dict, since each job has its own
`<姓名>-<应聘岗位>.png`. It resolves per the 7c answer: the user's uploaded image (`自定义上传`, one
path reused for every job), the rendered per-job `<姓名>-<应聘岗位>.png` (`AI调整`), or `None`
(`不发送`).

**Apply flow (per job):**

1. Open browser with the persistent Chrome profile (`./chrome_user_data/`)
2. Iterate over selected jobs
3. For each: visit detail page → click "立即沟通" → dismiss popups → click "继续沟通" → input
   greeting → send → (optional) send resume attachment
4. Log results to `{run_dir}/apply_log.json`

`send_resume_attachment()` tries three upload strategies (direct file input → JS injection → button
click fallback) after the greeting is sent. It uploads to **one** file input at a time and verifies
before moving on — pushing the file into every input at once sends duplicates.

### Send verification — never trust a click

Every send is confirmed by **reading the message back out of the chat DOM**. `status: 'applied'`
means a bubble was found; nothing else does.

| Signal | Meaning |
|---|---|
| `_input_greeting()` returns True | text was found *inside the input box* on re-read |
| `_click_send(dp, greeting)` returns True | a `_greeting_probe()` substring was found in the message list |
| `status: 'applied'` + `greeting_verified: True` | greeting bubble confirmed present |
| `status: 'partial'` | text is in the input box but no bubble — **not sent**, user must press Enter |
| `attachment_sent: True` | `img.message-image` count went **up** vs. the pre-send baseline |

Hard-won specifics (2026-08-13, verified against live DOM):

- **Never set `el.innerText` / `el.value` via JS.** BOSS's chat input is a framework-controlled
  component; JS assignment plus synthetic events leaves the internal state empty — the box *looks*
  filled and send dispatches nothing. Use `el.input()` (CDP real input) only.
- **`//button[@type='send']` does not send.** Enter (`dp.actions.type('\n')`) does. Enter is primary,
  the button is a fallback.
- **Image messages are invisible to `innerText`.** Count `img.message-image` elements instead, and
  exclude UI icons (`img.msg-blur`, 24×20) and avatars (72×72).
- A conversation preview reading `您正在与Boss XXX沟通` in the chat list means **zero messages were
  ever sent** in it. `[送达]` plus body text means the greeting landed.

### Apply order: HR activity first

`auto_apply_jobs` sorts before applying, with HR activity as the **primary** key:

```
key = (-hr_activity_sort_key(job), -match_score, company)
```

This is the one place activity outranks `match_score` — an unanswered message to a dormant HR is
worth less than a reply from an active one. Note this is the *opposite* weighting from the report's
tier lists, which keep `match_score` primary (see `references/matching.md` → HR Activity). To flip
it back, swap the first two elements of the tuple; nothing else depends on the order.

Activity is a crawl-time snapshot and is **not** refreshed here — `HR在线` was true when the crawl
ran, not necessarily now. When no job carries activity data (crawled without `-d`), the sort key
collapses to `-1` for every job and the order degrades to pure `match_score`; the run logs
`活跃度未采集，按匹配分排序` instead of `活跃度优先` so this is visible rather than silent.

**Truncation side effect:** `max_applications` slices *after* this sort, so raising activity to the
primary key changes *which* jobs get applied to whenever `len(qualified_jobs) > max_applications` —
not merely the sequence. A high-activity, lower-score job can now displace a top-score job with a
dormant HR. Stage 7 normally passes `max_applications=len(selected_jobs)`, so nothing is dropped;
the effect only appears when a caller sets a smaller cap.

## Greeting Fallback Chain

| Priority | Source | When |
|----------|--------|------|
| 1 | User-confirmed greeting | 7c `自定义`, or a 7d/7g-approved generation |
| 2 | `generate_greeting()` template | 7c `默认`, or an `AI生成` agent that returned nothing usable |
| 3 | `_default_greeting()` | Minimal greeting with position name only |

## Safety Rules

- 3-5 second delay between applications
- Max 10-20 applications per session
- Pause and notify user on captcha
- Reuse `./chrome_user_data/` login session (port 9222 — distinct from ShowCV's 9333)
- Log every application to `{run_dir}/apply_log.json`
