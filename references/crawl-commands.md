# Crawl Commands Reference

## Two-Phase Login Flow

### Phase 1a: Ensure Login

```bash
python scripts/boss_post_interactive.py --ensure-login
```

- Opens Chrome to BOSS Zhipin homepage
- Single XPath detection (no polling, no stdin interaction):
  - **Logged in**: `//a[@href='https://www.zhipin.com/web/geek/recommend']//img` exists → prints `[LOGIN_OK]`, closes browser
  - **Not logged in**: `//a[@class='btn btn-outline header-login-btn']` exists → prints `[LOGIN_NEEDED]`, keeps browser open, script exits
- If not logged in: user manually logs in, tells Claude "已登录", Claude re-runs `--ensure-login`
- Login state (cookies) persists to `./chrome_user_data/`

### Phase 1b: Execute Crawl

Login confirmed → run crawl. Script reuses persistent Chrome profile, login state auto-retained.

## CLI Parameter Reference

| Parameter | Description | Common values |
|-----------|-------------|---------------|
| `--ensure-login` | Phase 1a: detect login, persist session | Standalone command |
| `-m, --mode` | Job search mode | `custom` (keyword, recommended), `list` (by category) |
| `-p, --position` | Position / keywords | Comma-separated: `"Python,Java,数据分析"` |
| `-c, --city` | Target city | City name or `all`: `"北京,上海,深圳"` |
| `-n, --count` | Max per city per keyword | `20` (omit for unlimited) |
| `-d, --detail` | Crawl detail pages | Include for matching quality |
| `--no-sleep` | Disable request delay | Not recommended (anti-crawl protection) |
| `-y, --yes` | Skip confirmation prompt | Use in automated (Claude-driven) runs |
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

# Nationwide search
python scripts/boss_post_interactive.py -m custom -p "AI工程师" -c "all" -n 10 -d -y

# With filters: 3-5yr experience, bachelor's, 20-50K salary
python scripts/boss_post_interactive.py -m custom -p "Python后端" -c "北京" -n 20 -e "3-5年" -deg "本科" -s "20-50K" -d -y

# Multi-filter: experience + degree + company size
python scripts/boss_post_interactive.py -m custom -p "数据分析" -c "北京,上海" -n 15 -e "1-3年,3-5年" -deg "本科,硕士" --scale "100-499人,500-999人" -d -y

# Update data (first-time setup)
python scripts/boss_post_interactive.py -u
```

## Important Notes

- `-d` (detail pages) is critical — without it, job descriptions are missing and matching accuracy drops significantly. It is also the only source of the three HR-activity columns (`HR活跃度` / `HR在线` / `HR职位`): they come from `zpData.bossInfo` on the detail API, so a crawl without `-d` leaves them empty, the report shows 活跃度未采集, and Stage 7's apply ordering falls back to pure `match_score`
- HR activity is a **crawl-time snapshot**, never re-fetched. `HR在线` in particular is stale by the time you apply — treat `HR活跃度` as the durable signal. An empty value means "not collected", which is *not* the same as "offline"
- The CSV header is migrated in place: opening an older 17-column file rewrites it to the current 20-column schema (missing cells filled with `''`) and prints a `[迁移]` line. Existing rows are preserved but keep empty HR columns — re-crawl to populate them
- Default request delay is on (anti-crawl); use `--no-sleep` only if you understand the risk
- Persistent Chrome profile at `./chrome_user_data/` means login once, crawl many times
- CSV output lands in `./post_data/` directory
