#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书 / RedNote「收藏」图文下载器

流程：
1. 打开真实 Chromium 窗口，访问小红书首页
2. 若未登录，提示用小红书 App 扫描二维码登录（登录态持久化保存，下次免登录）
3. 进入「我」的个人主页 -> 「收藏」标签
4. 滚动加载全部收藏笔记
5. 逐篇打开笔记，抓取标题/正文/全部图片，按笔记保存到本地 output/ 目录

特性：断点续传、浏览器崩溃自动重启、屏蔽图片渲染省内存、国际账号(rednote.com)自适应。
用法：
  python xhs_download.py              # 全量：采集收藏列表 + 下载
  python xhs_download.py --use-cache  # 复用 favorites_links.json，跳过滚动直接下载
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = BASE_DIR / ".browser_profile"   # 持久化登录态
OUTPUT_DIR = BASE_DIR / "output"
LINKS_CACHE = BASE_DIR / "favorites_links.json"  # 收藏链接缓存（支持断点续传）
SITE_FILE = BASE_DIR / "site.txt"                # 记住账号实际域名（国际账号=rednote.com）
HOME_URL = "https://www.xiaohongshu.com"
USE_CACHE = "--use-cache" in sys.argv            # 复用已缓存的收藏链接，跳过滚动


def start_home_url() -> str:
    """优先使用上次登录成功的真实域名，保证登录态持久、自动重启免扫码。"""
    try:
        if SITE_FILE.exists():
            v = SITE_FILE.read_text(encoding="utf-8").strip()
            if v.startswith("http"):
                return v
    except Exception:
        pass
    return HOME_URL

OUTPUT_DIR.mkdir(exist_ok=True)


def done_note_ids() -> set:
    """扫描 output/ 已完成的笔记 id（文件夹名以 _<id> 结尾且含 content.txt）。"""
    done = set()
    for d in OUTPUT_DIR.glob("*"):
        if d.is_dir() and (d / "content.txt").exists():
            m = re.search(r"_([0-9a-f]{16,32})$", d.name)
            if m:
                done.add(m.group(1))
    return done


def note_id_of(url: str):
    m = re.search(r"/(?:explore|user/profile/[^/]+)/([0-9a-f]{16,32})", url)
    return m.group(1) if m else None


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def goto_robust(page, url: str, retries: int = 3, timeout: int = 90000):
    """容错导航：网络慢时只等 commit，超时则重试。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="commit", timeout=timeout)
            return
        except Exception as e:
            last_err = e
            log(f"  导航超时（第 {attempt}/{retries} 次）")
            await asyncio.sleep(1.5)
    raise last_err


def safe_name(name: str, max_len: int = 60) -> str:
    """把笔记标题清洗成安全的文件夹名。"""
    name = name.strip() or "untitled"
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", name)
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].strip(" .") or "untitled"


# ---------------------------------------------------------------------------
# 页面里执行的 JS：从 window.__INITIAL_STATE__ 读取笔记详情
# ---------------------------------------------------------------------------
EXTRACT_NOTE_JS = r"""
() => {
  const st = window.__INITIAL_STATE__;
  if (!st || !st.note) return null;
  const map = st.note.noteDetailMap || {};
  // 取当前页面的笔记 id
  let id = st.note.firstNoteId;
  if (!id) {
    const keys = Object.keys(map);
    if (keys.length) id = keys[0];
  }
  const entry = id ? map[id] : null;
  const note = entry && entry.note ? entry.note : null;
  if (!note) return null;

  const pickImg = (img) => {
    if (!img) return null;
    if (img.urlDefault) return img.urlDefault;
    if (img.urlPre) return img.urlPre;
    if (Array.isArray(img.infoList) && img.infoList.length) {
      // 优先无水印/原图
      const order = ['CRD_WM_WEBP', 'WB_DFT', 'WB_PRV'];
      for (const scene of order) {
        const hit = img.infoList.find(i => i.imageScene === scene && i.url);
        if (hit) return hit.url;
      }
      return img.infoList[img.infoList.length - 1].url;
    }
    return null;
  };

  const images = (note.imageList || []).map(pickImg).filter(Boolean);

  let videoUrl = null;
  try {
    const streams = note.video && note.video.media && note.video.media.stream;
    if (streams) {
      for (const k of Object.keys(streams)) {
        const arr = streams[k];
        if (Array.isArray(arr) && arr.length && arr[0].masterUrl) {
          videoUrl = arr[0].masterUrl; break;
        }
      }
    }
  } catch (e) {}

  return {
    id: note.noteId || id,
    type: note.type || 'normal',
    title: note.title || '',
    desc: note.desc || '',
    tags: (note.tagList || []).map(t => t.name).filter(Boolean),
    author: note.user ? (note.user.nickname || '') : '',
    authorId: note.user ? (note.user.userId || '') : '',
    time: note.time || note.lastUpdateTime || null,
    ipLocation: note.ipLocation || '',
    likes: note.interactInfo ? note.interactInfo.likedCount : null,
    images: images,
    video: videoUrl,
  };
}
"""

# 读取登录状态 + 自己的 user id（小红书用 Vue ref 包裹，需要解包 _value）
GET_LOGIN_STATE_JS = r"""
() => {
  const st = window.__INITIAL_STATE__;
  if (!st || !st.user) return {loggedIn: false, uid: null};
  const unref = (x) => (x && typeof x === 'object' && ('_value' in x)) ? x._value : x;
  const loggedIn = !!unref(st.user.loggedIn);
  const info = unref(st.user.userInfo) || {};
  const uid = info.userId || info.userid || info.id || null;
  return {loggedIn, uid};
}
"""


async def get_login_state(page):
    try:
        return await page.evaluate(GET_LOGIN_STATE_JS)
    except Exception:
        return {"loggedIn": False, "uid": None}


async def _open_login_qr(page):
    """点击『登录』弹出二维码登录框。"""
    for sel in ['.login-btn', '#login-btn']:
        try:
            await page.locator(sel).first.click(timeout=3000)
            return
        except Exception:
            continue
    try:
        await page.get_by_text("登录", exact=True).first.click(timeout=3000)
    except Exception:
        pass


async def wait_for_login(page, timeout_sec: int = 1800, refresh_every: int = 90) -> str:
    """弹出二维码，等待扫码登录。长时间等待，并定期刷新二维码避免过期。"""
    await _open_login_qr(page)
    log("=" * 56)
    log("请在弹出的【浏览器窗口】中，用『小红书 App』扫描二维码登录。")
    log("（App 内：我 -> 右上角菜单 -> 扫一扫）")
    log(f"将持续等待最多 {timeout_sec // 60} 分钟，二维码会自动刷新，登录后自动继续…")
    log("=" * 56)
    deadline = time.time() + timeout_sec
    last_refresh = time.time()
    while time.time() < deadline:
        state = await get_login_state(page)
        if state.get("loggedIn"):
            uid = state.get("uid")
            log(f"登录成功！用户 id = {uid}")
            return uid
        # 定期刷新页面 + 重新弹出二维码，避免二维码过期后一直等死
        if time.time() - last_refresh >= refresh_every:
            log("  二维码可能已刷新，正在重新生成…（请扫描最新的二维码）")
            try:
                await goto_robust(page, start_home_url(), retries=1, timeout=30000)
                await asyncio.sleep(2)
                await _open_login_qr(page)
            except Exception:
                pass
            last_refresh = time.time()
        await asyncio.sleep(2)
    raise TimeoutError("等待登录超时，请重试。")


async def switch_to_favorites_tab(page) -> bool:
    """点击『收藏 / Bookmarks』标签（兼容中英文，避免被弹层拦截）。"""
    # 关闭可能存在的登录/其它弹层
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await asyncio.sleep(0.5)
    result = await page.evaluate(
        r"""() => {
            const labels = ['收藏', 'Bookmarks', 'Bookmark', 'Collected',
                            'Collections', 'Saved', 'Favorites', 'Favourites'];
            const cand = [...document.querySelectorAll(
                '.reds-tab-item, [class*="tab-item"], div[role="tab"]')];
            const seen = cand.map(e => e.textContent.trim());
            // 1) 文本精确匹配
            let t = cand.find(e => labels.includes(e.textContent.trim()));
            // 2) 第二个 tab 兜底（个人主页通常是 笔记/收藏）
            if (!t && cand.length >= 2) t = cand[1];
            if (t) { t.click(); return {ok: true, labels: seen, clicked: t.textContent.trim()}; }
            return {ok: false, labels: seen, clicked: null};
        }"""
    )
    log(f"  页面标签: {result.get('labels')}")
    if result.get("ok"):
        log(f"  已点击标签: {result.get('clicked')}")
    return bool(result.get("ok"))


async def collect_favorite_links(page, base: str, self_uid: str) -> list:
    """进入自己主页 -> 收藏 tab -> 滚动收集所有笔记链接。"""
    profile_url = f"{base}/user/profile/{self_uid}"
    log(f"进入个人主页：{profile_url}")
    await goto_robust(page, profile_url)
    await asyncio.sleep(5)

    if await switch_to_favorites_tab(page):
        log("已点击『收藏』标签。")
    else:
        log("警告：未定位到『收藏』标签，将按当前页面采集。")
    await asyncio.sleep(4)
    log("开始滚动加载全部收藏笔记…")

    def extract_id(href: str):
        m = re.search(r"/(?:explore|user/profile/[^/]+)/([0-9a-f]{16,32})", href)
        return m.group(1) if m else None

    seen = {}
    stable_rounds = 0
    last_count = 0
    for i in range(300):  # 上限保护
        hrefs = await page.evaluate(
            r"""() => {
                const out = [];
                const items = document.querySelectorAll('section.note-item, div.note-item');
                items.forEach(it => {
                    // 优先取带 xsec_token 的链接（笔记跳转需要）
                    const links = [...it.querySelectorAll('a[href]')].map(a => a.getAttribute('href'));
                    const tokened = links.find(h => h && h.includes('xsec_token=') && h.match(/[0-9a-f]{16,}/));
                    const explore = links.find(h => h && h.includes('/explore/'));
                    if (tokened) out.push(tokened);
                    else if (explore) out.push(explore);
                });
                return out;
            }"""
        )
        for h in hrefs:
            full = urljoin(base + "/", h)
            nid = extract_id(full)
            if nid:
                # 带 token 的优先覆盖无 token 的
                if nid not in seen or "xsec_token=" in full:
                    seen[nid] = full
        count = len(seen)
        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_count = count
        log(f"  已发现 {count} 篇收藏笔记…")
        if stable_rounds >= 5:
            break
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await asyncio.sleep(1.8)

    links = list(seen.values())
    log(f"收藏笔记收集完成，共 {len(links)} 篇。")
    return links


def build_session_from_cookies(cookies) -> requests.Session:
    s = requests.Session()
    jar = {}
    for c in cookies:
        jar[c["name"]] = c["value"]
    s.cookies.update(jar)
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Referer": HOME_URL,
    })
    return s


def download_file(session: requests.Session, url: str, dest: Path) -> bool:
    if not url:
        return False
    if url.startswith("//"):
        url = "https:" + url
    try:
        r = session.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        log(f"    下载失败 {url[:80]} -> {e}")
        return False


def is_closed_error(e: Exception) -> bool:
    s = str(e).lower()
    return ("has been closed" in s or "target page" in s
            or "browser has been closed" in s or "crashed" in s)


RECYCLE_EVERY = 25  # 每处理 N 篇就重建标签页，释放内存，避免渲染进程崩溃


async def scrape_notes(page, context, links: list, session: requests.Session):
    """返回 'completed' 正常结束 / 'crashed' 浏览器异常需重启。"""
    total = len(links)
    done = done_note_ids()
    if done:
        log(f"检测到已下载 {len(done)} 篇，将自动跳过（断点续传）。")
    processed_since_recycle = 0
    for idx, url in enumerate(links, 1):
        nid_pre = note_id_of(url)
        if nid_pre and nid_pre in done:
            continue

        # 定期重建标签页，释放内存
        if processed_since_recycle >= RECYCLE_EVERY:
            try:
                new_page = await context.new_page()
                new_page.set_default_navigation_timeout(90000)
                new_page.set_default_timeout(30000)
                if not page.is_closed():
                    await page.close()
                page = new_page
                log("  （已重建标签页释放内存）")
            except Exception as e:
                if is_closed_error(e):
                    return "crashed"
            processed_since_recycle = 0

        log(f"[{idx}/{total}] 打开笔记：{nid_pre}")
        try:
            await goto_robust(page, url, retries=2, timeout=30000)
        except Exception as e:
            if is_closed_error(e):
                log("  浏览器已关闭，准备自动重启…")
                return "crashed"
            log(f"  打开失败：{e}")
            continue
        processed_since_recycle += 1
        # 等待 __INITIAL_STATE__ 注入笔记数据
        note = None
        for _ in range(10):
            await asyncio.sleep(0.8)
            try:
                note = await page.evaluate(EXTRACT_NOTE_JS)
            except Exception as e:
                if is_closed_error(e):
                    return "crashed"
                note = None
            if note and (note.get("images") or note.get("video") or note.get("desc")):
                break
        if not note:
            log("  未能解析笔记内容，跳过。")
            continue

        nid = note.get("id") or f"note_{idx}"
        title = note.get("title") or note.get("desc", "")[:30] or nid
        folder = OUTPUT_DIR / f"{idx:03d}_{safe_name(title)}_{nid}"
        folder.mkdir(parents=True, exist_ok=True)

        # 保存文本/元数据
        meta = {**note, "source_url": url}
        (folder / "metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        text_parts = []
        if note.get("title"):
            text_parts.append(note["title"])
        if note.get("desc"):
            text_parts.append(note["desc"])
        if note.get("tags"):
            text_parts.append("\n标签: " + " ".join("#" + t for t in note["tags"]))
        (folder / "content.txt").write_text(
            "\n\n".join(text_parts).strip() + "\n", encoding="utf-8")

        # 下载图片
        imgs = note.get("images") or []
        ok = 0
        for i, img_url in enumerate(imgs, 1):
            ext = ".jpg"
            if "png" in img_url.lower():
                ext = ".png"
            elif "webp" in img_url.lower():
                ext = ".webp"
            dest = folder / f"{i:02d}{ext}"
            if download_file(session, img_url, dest):
                ok += 1
        ntype = note.get("type")
        log(f"  类型={ntype} 图片 {ok}/{len(imgs)} 张已保存 -> {folder.name}")
        with open(OUTPUT_DIR / "_summary.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "index": idx, "id": nid, "title": title, "type": ntype,
                "images": ok, "folder": folder.name, "url": url,
                "has_video": bool(note.get("video")),
            }, ensure_ascii=False) + "\n")
        await asyncio.sleep(0.3)

    return "completed"


async def _block_media(route):
    """抓取阶段屏蔽图片/视频/字体：地址从 __INITIAL_STATE__ 取，再用 requests 下载。"""
    if route.request.resource_type in ("image", "media", "font"):
        try:
            await route.abort()
            return
        except Exception:
            pass
    try:
        await route.continue_()
    except Exception:
        pass


async def launch_ctx(p):
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )


async def run_session(p, attempt: int):
    """跑一轮浏览器会话；返回 ('completed'|'crashed', links_total)。"""
    from urllib.parse import urlparse
    context = await launch_ctx(p)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_navigation_timeout(90000)
        page.set_default_timeout(30000)
        await goto_robust(page, start_home_url())
        await asyncio.sleep(3)

        state = await get_login_state(page)
        if state.get("loggedIn") and state.get("uid"):
            self_uid = state["uid"]
            log(f"检测到已登录，用户 id = {self_uid}")
        else:
            self_uid = await wait_for_login(page)

        await asyncio.sleep(2)
        parsed = urlparse(page.url)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else HOME_URL
        log(f"使用站点域名：{base}")
        try:
            SITE_FILE.write_text(base, encoding="utf-8")
        except Exception:
            pass

        links = []
        if LINKS_CACHE.exists():
            try:
                links = json.loads(LINKS_CACHE.read_text(encoding="utf-8"))
                log(f"复用缓存的收藏链接：{len(links)} 篇。")
            except Exception:
                links = []
        if not links:
            links = await collect_favorite_links(page, base, self_uid)
            if links:
                LINKS_CACHE.write_text(
                    json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
                log(f"收藏链接已缓存到 {LINKS_CACHE.name}")
        if not links:
            log("没有发现任何收藏笔记。")
            return "completed", 0

        cookies = await context.cookies()
        session = build_session_from_cookies(cookies)
        await context.route("**/*", _block_media)

        result = await scrape_notes(page, context, links, session)
        return result, len(links)
    finally:
        try:
            await context.close()
        except Exception:
            pass


async def main():
    async with async_playwright() as p:
        attempt = 0
        total = 0
        prev_done = -1
        no_progress = 0
        while True:
            attempt += 1
            if attempt > 1:
                log(f"—— 第 {attempt} 次浏览器会话（自动续传）——")
            result, total = await run_session(p, attempt)
            done = len(done_note_ids())
            if result == "completed":
                break
            if total and done >= total:
                break
            # 防止无进展时无限重启
            no_progress = no_progress + 1 if done <= prev_done else 0
            prev_done = done
            if no_progress >= 4:
                log("连续多次重启无新进展，已停止。可稍后再次运行本脚本继续。")
                break
            log(f"浏览器异常退出，已完成 {done}/{total}，3 秒后自动重启继续…")
            await asyncio.sleep(3)

        done = len(done_note_ids())
        log("=" * 50)
        log(f"全部完成！已下载 {done} 篇，结果保存在：{OUTPUT_DIR}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("已手动中断。")
        sys.exit(1)
