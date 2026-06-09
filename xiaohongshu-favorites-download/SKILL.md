---
name: xiaohongshu-favorites-download
description: >-
  Download all image-text (图文) posts from a user's Xiaohongshu / RedNote
  「收藏」(favorites/bookmarks) to local disk via QR-code login. Logs in with a
  real Chromium window (QR scan), scrapes the 收藏 tab, and saves each note's
  images + title/body text + metadata. Use when the user asks to download / 备份 /
  导出 their 小红书 / RedNote 收藏 / 收藏夹 / favorites / bookmarks, batch-save
  xiaohongshu notes, or scrape xiaohongshu image-text posts locally.
disable-model-invocation: true
---

# Xiaohongshu / RedNote 收藏夹图文下载

A Playwright-based downloader: QR-login to 小红书/RedNote, open the user's own
profile 收藏 tab, collect every favorited note, then download each note's images
+ text. Battle-tested on 1000+ note collections with crash auto-restart and
resume. The bundled `scripts/xhs_download.py` does all the work.

## Quick start

```bash
# from the skill's scripts/ dir or any working dir you copy the script into
python3 -m venv .venv && . .venv/bin/activate
pip install playwright requests
python -m playwright install chromium

python xhs_download.py            # collect favorites list + download everything
python xhs_download.py --use-cache  # resume: reuse favorites_links.json, skip scrolling
```

A real Chromium window opens. **Tell the user to scan the QR code with the
小红书/RedNote App** (App: 我 → top-right menu → 扫一扫). Login state is persisted
in `.browser_profile/`, so subsequent runs usually skip the QR.

## Output layout (created next to the script)

```
output/
  001_<safe-title>_<noteId>/
    01.webp 02.webp ...   # all images of the note (video notes: cover only)
    content.txt           # title + body + #tags
    metadata.json         # full note data (author, time, likes, tags, url, video url)
  _summary.jsonl          # one line per downloaded note
favorites_links.json      # cached note links (enables --use-cache resume)
site.txt                  # remembered real domain (xiaohongshu.com or rednote.com)
.browser_profile/         # persisted login session
```

## How it works (key steps)

1. Open `site.txt` domain (or `xiaohongshu.com`) in a headed persistent Chromium.
2. Detect login from `window.__INITIAL_STATE__.user.loggedIn._value`; if not
   logged in, click 登录 to show the QR and poll until `loggedIn` is true.
3. Read self uid from `__INITIAL_STATE__.user.userInfo._value.userId`, go to
   `/user/profile/<uid>`, click the 收藏 tab, scroll until the note count is
   stable, collect note links (the ones carrying `xsec_token`).
4. For each note: navigate to its URL, read note data from
   `__INITIAL_STATE__.note.noteDetailMap[id].note`, save text/metadata, and
   download images with `requests` (using the browser's cookies).

## Critical gotchas (learned the hard way — don't re-discover these)

- **Vue ref unwrapping**: `__INITIAL_STATE__.user.loggedIn` / `userInfo` are Vue
  refs. Read the real value via `x._value` (helper `unref()` in the script).
  Naively reading them gives `[object Object]`.
- **`web_session` cookie exists even when logged OUT** (guest session). Never use
  it as the login signal — use `loggedIn._value` instead.
- **Never read uid from a random `a[href^="/user/profile/"]`** — homepage feed
  cards link to other users. Use `userInfo._value.userId`.
- **International / RoW accounts redirect to `rednote.com`**, and the UI is in
  **English**: the 收藏 tab label is **"Save"** (also handle Bookmarks/Saved/
  Collected/Favorites). The script matches multilingual labels and falls back to
  the 2nd tab. The real domain is captured from `page.url` after login and saved
  to `site.txt` so restarts stay logged in.
- **Note cards**: selector `section.note-item, div.note-item`. The link that
  works for navigation is the one with `xsec_token=` (e.g.
  `/user/profile/<uid>/<noteId>?xsec_token=...&xsec_source=pc_collect`).
- **Renderer OOM crash**: a profile page with 1000+ cards in the DOM can crash
  Chromium. Mitigations in the script: block `image/media/font` requests during
  scraping (image URLs come from JSON, downloaded separately via `requests`),
  recycle the tab every 25 notes, and auto-restart the whole browser session on
  "Target/browser has been closed", resuming from disk.
- **Big collections take hours** (~6–7 notes/min, so ~2.5h for 1000). Run it in
  the background and let resume handle interruptions. Keep the window open.
- **Resume** = `done_note_ids()` scans `output/` for folders ending in `_<id>`
  with a `content.txt` and skips them; `favorites_links.json` caches the list.
  Re-running (with or without `--use-cache`) continues where it left off and also
  retries previously skipped (deleted/unavailable/parse-failed) notes.

## Running for a long job

Run unbuffered to a log so progress is visible (a `grep` pipe buffers output):

```bash
nohup python -u xhs_download.py --use-cache > run.log 2>&1 &
tail -f run.log
# progress: ls -1 output | grep -vE '_summary|favorites' | wc -l
```

## Notes

- "图文" = image-text notes (`type: "normal"`) → all images saved. Video notes
  (`type: "video"`) save the cover image + metadata (video master URL is recorded
  in `metadata.json`).
- macOS/Linux/Windows all work (Playwright Chromium). Paths use `/`.
- If login keeps timing out, the QR wasn't scanned within 300s — just re-run.
