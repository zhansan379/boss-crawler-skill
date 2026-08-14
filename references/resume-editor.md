# Resume Editor Reference (ShowCV)

An embedded snapshot of the [ShowCV](https://github.com/) Markdown resume editor, served locally
and opened in an isolated Chromium via DrissionPage. Migrated from the upstream `showcv-launch`
skill. No `pnpm install`, no node, no ShowCV source checkout required — `app/` ships in this repo.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/showcv/serve.py` | Static server for `app/` with SPA fallback. Prints `SHOWCV_READY <url>` |
| `scripts/showcv/launch.py` | Opens an isolated Chromium at the URL and verifies ShowCV actually loaded |
| `scripts/showcv/storage.py` | Moves resume data between origins and to/from disk (`dump`/`load`/`move`) |
| `scripts/showcv/import_md.py` | Batch-imports `.md` files as resumes by feeding the hidden file input |
| `scripts/showcv/export_images.py` | Exports resumes as PNG/zip via the `/export` direct link |
| `scripts/showcv/delete_resumes.py` | Deletes resumes via the `/delete` direct link, with a backup first |
| `scripts/showcv/sync_app.py` | Re-syncs `app/` from a ShowCV repo's `dist/`. Only needed to upgrade |
| `scripts/showcv/_browser.py` | Shared browser connection logic for the scripts above |
| `scripts/showcv/_resumes.py` | Shared resume list reader + `--id`/`--name` → id resolution |

Run them as scripts, not modules — they import `_browser` by relying on the script's own
directory being on `sys.path`, so `python scripts/showcv/launch.py ...` works but
`python -m scripts.showcv.launch` does not.

## Bridging the Editor into Stage 3

The editor stores each resume's body as **raw Markdown** in `localStorage`. That means a resume
written in the editor can feed Stage 3 parsing directly — no PDF/Word parsing needed.

```bash
python scripts/showcv/storage.py dump --url http://127.0.0.1:3090 --out {run_dir}/showcv-resume.json
```

The dumped file's shape (verified against real data):

```json
{
  "key": "showcv-resume",
  "origin": "http://127.0.0.1:3090",
  "value": {
    "version": 0,
    "state": {
      "theme": "light",
      "previewMode": "paginated",
      "currentResumeId": "1786528558630-pjwry3t",
      "resumes": [
        {"id": "...", "name": "张三 - 副本 (3)", "content": "# 张三\n\n电话: ...",
         "templateId": "...", "settings": {}, "createdAt": 0, "updatedAt": 0}
      ]
    }
  }
}
```

To use it:

1. **Pick the right resume — do not just take `resumes[0]`.** Users accumulate copies: real data
   had 8 entries with near-identical names (`张三`, `张三 - 副本`, `张三 - 副本 (3)` …). Locate the
   active one via `state.currentResumeId`, then confirm with `AskUserQuestion` showing each
   `name` + `updatedAt`. The field is `currentResumeId` — **not** `activeResumeId`.
2. Write that entry's `content` to `{run_dir}/resume_text.txt` — it *is* `resume_text`.
3. Continue at Stage 3 (parse → profile.json) as normal, including the Stage 3.5b cross-validation
   run.

`currentResumeId` can be `null` (no resume ever selected). Ask the user in that case rather than
guessing.

## Moving Resume Data Between Origins

`localStorage` is partitioned by origin (`scheme://host:port`) — a hard part of the browser
storage model. **No launch flag makes two origins share one store** (`--disable-web-security`
only affects cross-origin *requests*, not storage partitioning). So "I changed ports and want my
old resume" requires an explicit move:

```bash
# origin → disk (defaults to assets/showcv-resume.json)
python scripts/showcv/storage.py dump --url http://127.0.0.1:3090

# disk → origin
python scripts/showcv/storage.py load --url http://127.0.0.1:3091

# origin → origin (one step; source is kept — this is a copy, not a cut)
python scripts/showcv/storage.py move --from http://127.0.0.1:3090 --to http://127.0.0.1:3091
```

- **Overwriting a non-empty target is refused by default.** Pass `--force` to override; `dump` the
  target first if you want a backup. **`--force` is riskier than it looks — see the debug-port
  section below; the guard is per-origin and cannot see a profile mismatch.**
- **Writes must use the main tab, not a temp tab.** If a tab holding stale state is still open on
  the target origin, zustand persist writes its whole in-memory state back to `localStorage` on
  the next mutation, clobbering what was just injected. So `write_origin` navigates the *main* tab
  to the target, writes, then refreshes. Reads don't have this problem — they use a temp tab.
- **The title check in `open_app` is not optional.** Observed in practice: another service (a
  directory listing) was squatting on the target port, and the check blocked the write. Without it
  the resume would have been written to the wrong origin and looked like it succeeded.
- The disk file holds **parsed JSON** (human-readable, diffable); `load` re-serializes it to the
  string zustand expects. It doubles as a resume backup.

## Batch-Importing Markdown

`import_md.py` bulk-loads `.md` files as resumes. It drives the **hidden file input**, not the
"导入 md" button:

```bash
# a directory of .md files (add -r to recurse)
python scripts/showcv/import_md.py --url http://127.0.0.1:3090 resumes/

# specific files, preview only — does not touch the browser
python scripts/showcv/import_md.py --url http://127.0.0.1:3090 a.md b.md --dry-run
```

**Why not click the button.** Its `onClick` is just `m.current?.click()`, which opens the native
OS file dialog — a window CDP cannot reach. The button's actual target is
`<input type="file" accept=".md,.markdown" multiple class="hidden">`, and DrissionPage routes
file inputs through `DOM.setFileInputFiles` (`_elements/chromium_element.py:678`), addressed by
`backendNodeId` with **no visibility check**. So a `hidden` input takes paths directly, and the
CDP call fires a real `change` event, so React's `onChange` runs as usual.

Frontend constraints, all read out of `app/assets/index-*.js` (mirrored as constants in the
script — re-check them after any `sync_app.py` upgrade):

| Constraint | Value | Consequence |
|---|---|---|
| Extensions | `.md`, `.markdown` | Anything else is dropped **silently** by the frontend |
| Files per batch | 50 | Over the limit the frontend **rejects the whole batch**, not the excess |
| `5 * 1024 * 1024` | 5 MB | Doubles as per-file cap *and* total `localStorage` quota |
| Duplicate names | `名字 (2)`, `(3)` … | Import is purely additive — an existing resume is never overwritten |
| Resume name | filename minus extension | Empty after trimming → `未命名简历` |

Design notes worth knowing before editing the script:

- **It batches at 50 automatically.** The frontend's cap rejects the entire selection, so passing
  55 files in one `input()` call imports *nothing*.
- **Verification is by name, never by count.** A brand-new origin ships one default resume that is
  still unpersisted at first mount, so the baseline reads as 0 and that resume materialises with
  the first import. Counting alone lets it substitute for a file that failed, disguising a partial
  failure as success. The script matches each new name back to a source filename (exact first,
  then the ` (n)` variant) and reports leftovers separately.
- **Landing is polled from `localStorage`.** The frontend reads files with `FileReader`
  asynchronously; `input()` returning means the paths were delivered, not that anything was saved.
- **Writes use the main tab**, for the same reason as `storage.py`'s `write_origin`.
- **It prints the real profile**, resolved from the browser PID — see below for why the configured
  one cannot be trusted.
- On timeout it makes a best-effort grab of `[data-sonner-toast]` text, which is where the
  frontend puts "本地存储空间不足" and per-file skip reasons. Toasts auto-dismiss, so a miss is
  normal and never fails the run.

## URL-Driven Export and Delete

The app has two direct-link routes that do things the UI cannot. Both are parsed in
`app/assets/index-*.js` (`qD` for export, `sO` for delete) and both read resumes out of the
**current browser's** `localStorage`, so an id is only meaningful in the profile+origin that saved
it.

| | `/export` | `/delete` |
|---|---|---|
| `id` | repeatable, also comma-separated | same |
| `all=1` \| `true` \| `yes` | all resumes; `id=all` is equivalent | same |
| id omitted | falls back to the **current** resume | **errors** — never guesses |
| unknown id | silently ignored, the rest still run | silently ignored, the rest are still deleted |
| other params | `mode=flat\|paginated` (default paginated), `scale=1\|2\|3` (illegal → 2) | `confirm=1` deletes on mount, otherwise a confirmation page renders first |

The asymmetry on a missing `id` is deliberate upstream (`deleteUrlService.ts:18-19`): guessing wrong
on an export costs nothing, guessing wrong on a delete cannot be taken back.

### `export_images.py`

```bash
python scripts/showcv/export_images.py --url http://127.0.0.1:3090            # current resume
python scripts/showcv/export_images.py --url http://127.0.0.1:3090 --name 张三 --mode flat
python scripts/showcv/export_images.py --url http://127.0.0.1:3090 --all --scale 3 --out D:/tmp/cv
```

- **The download directory must be set before navigating.** The page exports itself on mount, and
  DrissionPage only tracks downloads that start after its `Browser.setDownloadBehavior` callback is
  installed. Set it afterwards and you race the page; losing the race looks like
  `wait.downloads_done()` returning immediately with nothing on disk.
- Both the browser-level and tab-level download paths are set. Chrome writes the file under the
  *browser* path (`allowAndName` gives it a GUID name) and DrissionPage renames it into the *tab*
  path — leave them different and half the artifacts end up in the cwd.
- Naming comes from `FD`/`ID`: a single image is `<resume name>.png`; anything more (multi-page
  `paginated`, or several resumes) is zipped as `showcv-images-<YYYYMMDD>.zip`. Verified: a 2-page
  resume yields `<name>-1.png` + `<name>-2.png` inside the zip.
- `--scale 4` is **rejected**, unlike the page which silently falls back to 2. On a command line
  that value is a typo, and a silent fallback would hand back 2× images labelled as 3×.
- Completion is read off the page (`正在生成图片` → `已下载 <file>`, or the error state), then
  cross-checked against the directory listing. The page only knows it *called* the download.
- A failure after navigation keeps the tab open — the page carries the only 「重新生成」 button.

### `delete_resumes.py`

```bash
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name 张三-后端 --dry-run
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name 张三-后端 --yes
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --all --yes
```

`localStorage` is the only copy of these resumes, so the script adds three layers on top of the
page's own guard:

1. **A full backup before deleting**, in `storage.py dump` format under
   `assets/showcv_backups/`, and the exact `storage.py --force load` command to restore it is
   printed. The page's 「撤销」 keeps its snapshot in a `useRef` only, so a refresh — or navigating
   away — loses it. The backup covers the whole origin, not just the targets, so recovery is one
   command and not a merge script.
2. **It goes through the confirmation page instead of `confirm=1`.** The page lists the resumes
   *it* thinks it will delete; the script compares that list with what it resolved locally and, on
   any mismatch, never clicks — nothing is deleted. A mismatch means something else changed the
   data in between (another tab, another script).
3. **`--yes` is mandatory**; `--dry-run` or no flag at all only prints the plan.

Further deliberate differences from the page's behaviour:

- **Unknown `--id`/`--name` aborts the whole run.** The page ignores unmatched ids and deletes the
  rest, mentioning it in a corner line. One mistyped name would then quietly delete fewer resumes
  than intended, and "thought it was deleted but it wasn't" surfaces much too late.
- **A duplicate name is an error, not a coin flip** (`_resumes.resolve` — use `--id` instead).
- **It uses the main tab**, like `storage.py`'s `write_origin`: a second tab on that origin holding
  stale state would resurrect the deleted resumes on its next mutation via zustand `persist`.
- **Verification is by id against `localStorage`**, not by the page's own count. Deleting
  everything makes the frontend create a fresh blank resume immediately; id-based checking does not
  mistake that new `我的简历` for a `我的简历` that was supposed to be gone.
- The result page is left open by default so 「撤销」 is still reachable; `--return-to-editor`
  navigates back and says so.

## Debug Port 9333 Is Shared (the adoption trap)

`_browser.py` hardcodes `DEBUG_PORT = 9333`. The upstream `showcv-launch` skill (at
`D:\Project\open-source project\ShowCV\.claude\skills\showcv-launch`) hardcodes the same value, so
whichever browser starts first owns the port.

**`set_user_data_path` is only honored when DrissionPage actually launches a browser.** If one is
already listening on 9333 it is **adopted** and the profile setting is silently discarded — while
`launch.py` still prints `profile=…/assets/showcv_profile`, because it prints the configured value,
not the effective one.

Confirmed twice on 2026-08-12: `launch.py` reported the repo's sandbox profile while actually
driving the ShowCV project's `.profile`, and `storage.py dump` then returned 8 real resumes from an
origin verified empty in the repo's own profile minutes earlier.

**The most dangerous operation is `storage.py load --force`.** Its overwrite guard is per-*origin*
and structurally cannot detect a profile mismatch, so a load aimed at the sandbox can destroy real
resumes while reporting the wrong path as success. `delete_resumes.py --all` is the same class of
risk — the wrong profile means deleting somebody else's resumes and backing up somebody else's
data. `import_md.py` is additive and therefore much safer, but it too can write into the wrong
profile.

Before any write, identify the browser that is actually there:

```powershell
netstat -ano | Select-String ":9333\s" | Select-String LISTENING
(Get-CimInstance Win32_Process -Filter "ProcessId = <pid>").CommandLine   # read --user-data-dir
```

`import_md.py`, `export_images.py` and `delete_resumes.py` all do this automatically
(`_browser.real_profile()`) and print the resolved path as their first line. It resolves via PID
rather than CDP `Browser.getBrowserCommandLine`, because that command requires the browser
to have been started with `--enable-automation` — a flag we can only add when *we* launch it, which
is precisely the case that isn't at risk. The PID comes from `SystemInfo.getProcessInfo` and is
correct for adopted browsers too.

Left unfixed deliberately (user's call, 2026-08-12): documented rather than repointed to a
per-project port.

## Key Design (read before editing the scripts)

- **The port must not drift.** Resume data lives in `localStorage`, which is origin-scoped — change
  the port and the user's saved resume "disappears". So `serve.py` probes 3090 first and, if a
  ShowCV service is already there, reuses that port and exits instead of starting a second one on
  a new port.
- **`ThreadingHTTPServer` cannot be downgraded to `HTTPServer`.** One page load is dozens of
  requests including 14MB of fonts; single-threaded serialization hangs it.
- **`allow_reuse_address = False` is deliberate.** On Windows, `SO_REUSEADDR` lets two processes
  bind the same port, so leaving it on lets someone steal a port we already hold. `server_bind`
  additionally sets `SO_EXCLUSIVEADDRUSE`.
- **Port-occupancy checks must use connect, not a failed bind.** Measured: with
  `python -m http.server` holding a port, our `bind()` still "succeeds" but every request lands in
  the other process — looks healthy, serves someone else's content. Hence `is_listening()` before
  binding.
- **Fixed debug port 9333 + a dedicated profile** (`assets/showcv_profile/`), not DrissionPage's
  `auto_port()`. `auto_port()` uses a throwaway profile and deletes it on exit, so every launch
  would start empty. The fixed port also makes launching idempotent — an existing instance is
  adopted rather than opening a second window. **That adoption cuts both ways: see the 9333
  section below.**
- **The profile is separate from `assets/chrome_user_data/`** (the BOSS Zhipin login state) on
  purpose: the resume editor should not run in a browser logged into zhipin.com. `boss_crawler`
  uses DrissionPage's default debug port 9222, so the two never collide.
- **Paths with an extension are left to 404 on purpose** — no fallback. Silently returning
  `index.html` would disguise "a font didn't get copied" as a 200.


## Known Limitations

- **No backend.** The embedded build is purely static; `/api/*` does not exist → the **Share** and
  **AI optimize** buttons will fail. Those need the ShowCV repo running
  `pnpm build:server && node dist/server/index.js` (port 3070).
- **`app/` is a snapshot.** Changes to ShowCV source do not follow automatically; run
  `pnpm build` there, then `scripts/showcv/sync_app.py --dist <ShowCV>/dist`.
- **Slightly slow first paint when offline.** `index.html` preconnects to Google Fonts; with no
  network it waits out the `display=swap` timeout. Functionally harmless.
- **`/fonts/PingFangTC-Regular.otf` always 404s.** The CSS references it but upstream's
  `dist/fonts/` doesn't ship it — a pre-existing upstream issue, not introduced here. The font
  falls back.
- **Resumes live only in this machine's browser.** Switching profile, clearing `localStorage`, or
  changing port all make previous resumes invisible. Port changes can be fixed with
  `storage.py move`; profile changes need `dump` then `load`. An `/export` link sent to someone
  else won't open for them.
- **`assets/showcv_profile/` grows** (~28MB after first launch — Chrome's own cache). Deleting it
  is safe, **but any resume stored inside is deleted with it.**

## Unattended Automation Hook

`launch.py` documents the interface at the bottom of the file: inject `localStorage`'s
`showcv-resume` key, then open `/export?id=all&mode=paginated&scale=2` — that direct link triggers
a batch export before the frontend mounts. Use `storage.py` to inject/extract the data, and
`export_images.py` for the export half, which already implements that link (plus the download
tracking the raw URL leaves to you).
