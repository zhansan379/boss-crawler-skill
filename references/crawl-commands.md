# Crawl Commands Reference

The `crawl` stage normally runs through `pipeline.py --from crawl`, which builds this argv from
`crawl_params.json` and appends `--run-dir`. Reach for `boss_post_interactive.py` directly when you
need a one-off crawl outside a run, or to debug what the pipeline assembled.

## Two-Phase Login Flow

### Phase 1a: Ensure Login

```bash
python scripts/boss_post_interactive.py --ensure-login
```

- Opens Chrome to BOSS Zhipin homepage
- Single XPath detection, no polling. Whether it blocks on stdin depends on the caller: with a TTY it
  waits for Enter and rechecks (up to 5 times, `q` aborts); when stdin is piped — the skill path — it
  prints its verdict and exits without ever calling `input()`
  ([cli.md §3](cli.md)). `test_ensure_login.py` locks that branch.
  - **Logged in**: `//a[@href='https://www.zhipin.com/web/geek/recommend']//img` exists → prints `[LOGIN_OK]`, closes browser
  - **Not logged in**: `//a[@class='btn btn-outline header-login-btn']` exists → prints `[LOGIN_NEEDED]`, keeps browser open, script exits
- If not logged in: the user logs in manually and says 已登录 — then re-run `--ensure-login` to
  confirm rather than assuming it worked
- Login state (cookies) persists to `assets/chrome_user_data/` (skill-root-relative, not cwd)

### Phase 1b: Execute Crawl

Login confirmed → run crawl. Script reuses persistent Chrome profile, login state auto-retained.

## CLI Parameter Reference

| Parameter | Description | Common values |
|-----------|-------------|---------------|
| `--ensure-login` | Phase 1a: detect login, persist session | Standalone command |
| `-m, --mode` | Job search mode | `custom` (keyword, recommended), `list` (by category) |
| `-p, --position` | Position / keywords | Comma-separated: `"Python,Java,数据分析"` |
| `-c, --city` | Target city | Comma-separated names: `"北京,上海,深圳"`. `全国` / `不限` → the single nationwide code. `all` → **every one of the 374 cities**, crawled one by one — almost never what you want |
| `-n, --count` | Max per city per keyword | `20` (omit for unlimited) |
| `-d, --detail` | Crawl detail pages | Include for matching quality |
| `--no-sleep` | Disable request delay | Not recommended (anti-crawl protection) |
| `-y, --yes` | Skip the stdin confirmation prompt | Always set in unattended runs — there is no terminal to answer it |
| `-j, --jobType` | Job type filter | `全职,实习,兼职` |
| `-s, --salary` | Salary range filter | `3K以下,3-5K,5-10K,10-20K,20-50K,50K以上` |
| `-e, --experience` | Experience filter | `应届生,1年以内,1-3年,3-5年,5-10年,10年以上,在校生,经验不限` |
| `-deg, --degree` | Education filter | `大专,本科,硕士,博士,高中,中专/中技,初中及以下` |
| `--scale` | Company size filter | `0-20人,20-99人,100-499人,500-999人,1000-9999人,10000人以上` |
| `-u, --update-data` | Update position/city JSON data | Run on first use |
| `--list-positions` | List available positions | Exploration only |
| `--list-cities` | List available cities | Exploration only |

## Command Examples

```bash
# Ensure login first
python scripts/boss_post_interactive.py --ensure-login

# Quick search: Python, Beijing, 20 jobs, with details
python scripts/boss_post_interactive.py -m custom -p "Python" -c "北京" -n 20 -d -y

# Multi-position, multi-city
python scripts/boss_post_interactive.py -m custom -p "Python,Java,数据分析" -c "北京,上海,深圳" -n 15 -y

# Nationwide search — one crawl against BOSS's 全国 code.
# NOT `-c all`, which loops all 374 cities separately.
python scripts/boss_post_interactive.py -m custom -p "AI工程师" -c "全国" -n 10 -d -y

# With filters: 3-5yr experience, bachelor's, 20-50K salary
python scripts/boss_post_interactive.py -m custom -p "Python后端" -c "北京" -n 20 -e "3-5年" -deg "本科" -s "20-50K" -d -y

# Multi-filter: experience + degree + company size
python scripts/boss_post_interactive.py -m custom -p "数据分析" -c "北京,上海" -n 15 -e "1-3年,3-5年" -deg "本科,硕士" --scale "100-499人,500-999人" -d -y

# Update data (first-time setup)
python scripts/boss_post_interactive.py -u
```

## 配置预设 (`scripts/preferences.py`)

The single biggest time saver for a returning user. The 2026-08-14 run spent 12 of its 46 minutes
(30%) on interaction round-trips, most of it re-answering the same city / keyword / degree / mode
questions from the previous run. A preset makes the `infer` confirmation ask nothing.

```bash
# Path C's first step. Exit 0 = preset exists, 1 = none.
python scripts/preferences.py show

# Which fields the preset is missing (exit 1 when there are any) — ask only about those
python scripts/preferences.py missing

# Emit a standalone crawl command from the saved preset (manual escape hatch)
python scripts/preferences.py crawl-args

# 补写单个字段：save 默认合并，没给出的字段保持原值
python scripts/preferences.py save --top 20

# Save after the user confirms the infer parameters（整份重写 → --replace）
python scripts/preferences.py save --replace --keywords "AI应用开发,大模型应用开发" --city "太原" \
    --count 20 --degree "本科" --match-mode deep --top 10

# Forget it (the user asks to reset, or the market/target changed)
python scripts/preferences.py clear
```

| Subcommand | Exit code | Use |
|---|---|---|
| `show` | `1` when no preset | Branch on the exit code, not on parsing the text |
| `missing` | `1` when fields are missing | The fill-the-gaps question list for path C |
| `crawl-args` | `1` when no preset | Prints one `boss_post_interactive.py …` line — run it as-is |
| `save` | `0` | **Merges** by default — only the fields you pass are written, the rest keep their stored values; there is only ever one preset. `--replace` rewrites the whole file (used by `infer --save`, which has just printed the full set for confirmation); under merge you cannot clear a single field by omitting it. Short forms mirror the crawler's own flags (`-c -p -m -n -deg -e -s -j`). Detail crawling is on by default — pass `--no-detail` to turn it off |
| `clear` | `0` | Idempotent — no error when the file is already gone |

Stored at `assets/preferences.json`, keyed by `SCHEMA_VERSION`, stamped with `saved_at`.

`crawl_argv()` is the **single** place the 10 preset fields get mapped onto `-p -c -n -d -deg -e -s
-j --scale`. `crawl-args` renders it as a shell string; `pipeline.py`'s crawl stage feeds the same
list to `subprocess` from `crawl_params.json`. Two assembly points would mean one of them silently
missing a new field.

**Presets never expire.** `show` reports the age (`已保存 23 天`) and stops there. An expiry rule
would be a gate that fires on a calendar rather than on anything the user did — and re-confirming an
unchanged city every 30 days is exactly the round-trip this feature removes. The age is printed so
the user can notice a stale preset themselves and say so.

**The key whitelist is a security boundary, not typo protection.** `preferences.json` is a plain
file a user (or anything with write access to the repo) can edit. `load()` drops every key outside
`ALLOWED_KEYS` and every value of the wrong type, which is what keeps a hand-added
`{"auto_apply": true}` or `{"skip_gate_send": true}` from becoming an off switch for `gate:send`. The
preset schema has **no** field for which jobs to apply to, what greeting to send, or whether to send —
those live only in `gate:jobs` and `gate:send`, in-session, every run. `scripts/test_preferences.py`
group [3] locks this; if you add a key, that test is the thing that must still pass.

**`-y` is always set in `crawl-args`, and that is not a bypass.** `-y` only suppresses
`boss_post_interactive.py`'s own stdin prompt, which an unattended run cannot answer anyway (there
is no terminal attached). Crawling is a read operation. The confirmation that matters is `gate:send`,
and no preset field reaches it.

## Important Notes

- `-d` (detail pages) is critical — without it, job descriptions are missing and matching accuracy drops significantly. It is also the only source of the three HR-activity columns (`HR活跃度` / `HR在线` / `HR职位`): they come from `zpData.bossInfo` on the detail API, so a crawl without `-d` leaves them empty, the report shows 活跃度未采集, and `apply.py`'s send ordering falls back to pure `match_score`
- HR activity is a **crawl-time snapshot**, never re-fetched. `HR在线` in particular is stale by the time you apply — treat `HR活跃度` as the durable signal. An empty value means "not collected", which is *not* the same as "offline"
- The CSV header is migrated in place: any header that differs from the current `CSV_FIELDS` is rewritten to it (missing cells filled with `''`) and prints a `[迁移]` line. The check is a whole-list comparison, not a column count, so it catches reordering too. Existing rows are preserved but keep empty HR columns — re-crawl to populate them. `CSV_FIELDS` in `boss_crawler/config.py` is the only place the schema is defined; don't restate its length here
- Default request delay is on (anti-crawl); use `--no-sleep` only if you understand the risk
- Persistent Chrome profile at `assets/chrome_user_data/` means login once, crawl many times
- CSV output lands in `assets/post_data/` — `custom/{关键词}_{城市}.csv` for keyword mode, one directory per category for list mode. The pool is cumulative across runs, which is why a row count there is not this run's yield
