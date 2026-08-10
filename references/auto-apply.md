# Auto-Apply Reference

## Five Sub-Steps (Never Skip Confirmation)

### 7a: Display Recommended Jobs

After matching, present a table:

```
📋 Recommended Jobs (N qualified)

| # | Company | Position | Salary | Match | Difficulty | Reason |
|---|---------|----------|--------|-------|------------|--------|
| 1 | XX科技  | Java开发  | 15-25K | 85%   | 易         | 技能高度匹配 |
| 2 | YY公司  | 全栈工程师 | 20-30K | 78%   | 中         | 技术栈匹配 |
```

Include: match strengths, potential gaps, total application count, and priority order.

### 7b: User Confirms Jobs

Use `AskUserQuestion`:
- Apply to all qualified? Or select specific ones?
- Optimize resume for any before applying?

### 7c: Generate & Confirm Greetings

For each selected job, Claude generates a personalized greeting from the deep analysis results.

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

**Display format — show one job at a time:**

```
📝 Greeting Preview

[1/3] XX科技 - Java开发 (285 chars)

您好，我是冯雷霆，2026届计算机科学与技术专业本科应届生，
对贵公司的「Java开发」岗位非常感兴趣。
...
希望能有机会进一步沟通，期待您的回复！

---
```

Use `AskUserQuestion` for each:
- ✅ Confirm, use this greeting
- ✏️ Edit (user provides modified version in "Other")
- ⏭ Skip this job

If user edits, use their version verbatim.

### 7c-2: Resume Image Attachment

Use `AskUserQuestion`:

> 📎 Upload resume image as attachment?
> - 📄 Upload (provide image path in "Other")
> - ⏭ Skip, send greeting only

If user provides a path:
- Verify file exists (`os.path.exists(path)`)
- Accept `.jpg/.jpeg/.png/.gif/.webp/.bmp`
- If file missing, ask user to re-enter — do not skip
- Pass as `resume_file_path` to `auto_apply_jobs()`

The `send_resume_attachment()` function tries three upload strategies (direct file input → JS injection → button click fallback) after sending the greeting.

### 7d: Execute Apply

```python
from resume_matcher import auto_apply_jobs

results = auto_apply_jobs(
    qualified_jobs=selected_jobs,
    _profile=profile,
    max_applications=len(selected_jobs),
    greetings=greetings,              # keyed by job link
    resume_file_path=resume_image,    # None if skipped
    output_dir=run_dir,
)
```

**Apply flow (7 steps per job):**
1. Open browser with persistent Chrome profile (`./chrome_user_data/`)
2. Iterate over selected jobs
3. For each: visit detail page → click "立即沟通" → dismiss popups → click "继续沟通" → input greeting → send → (optional) send resume attachment
4. Log results to `{run_dir}/apply_log.json`

## Greeting Fallback Chain

| Priority | Source | When |
|----------|--------|------|
| 1 | User-confirmed greeting | Stage 7c confirmed or edited version |
| 2 | `generate_greeting()` template | Auto-generates from `ResumeProfile` education/skills/projects |
| 3 | `_default_greeting()` | Minimal greeting with position name only |

## Safety Rules

- 3-5 second delay between applications
- Max 10-20 applications per session
- Pause and notify user on captcha
- Reuse `./chrome_user_data/` login session
- Log every application to `{run_dir}/apply_log.json`
