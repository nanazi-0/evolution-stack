"""探测 chanxuan 榜单页背后调用了哪些接口。

连到已经跑着的 Edge（CDP 9222），打开榜单页，把 XHR/fetch 响应记下来。
目的：找到返回结构化榜单数据的接口，避免用 DOM 猜字段。

  python probe_api.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"D:\start\ds-harness\work\_scrape\chanxuan-api")
CDP = "http://127.0.0.1:9222"

PAGES = [
    ("rank-product", "https://www.chanxuan.com/rising-rank/clip-product.html"),
    ("rank-author", "https://www.chanxuan.com/rising-rank/clip-author.html"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]

        def on_response(resp):
            try:
                url = resp.url
                if "chanxuan.com" not in url:
                    return
                ct = (resp.headers or {}).get("content-type", "")
                if "json" not in ct.lower():
                    return
                body = resp.text()
                if len(body) < 40 or len(body) > 3_000_000:
                    return
                captured.append({"url": url, "status": resp.status, "len": len(body), "body": body})
            except Exception as exc:  # noqa: BLE001
                captured.append({"url": resp.url, "error": f"{type(exc).__name__}: {exc}"})

        page = ctx.new_page()
        page.on("response", on_response)
        for name, url in PAGES:
            captured.clear()
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {url} -> {exc}", flush=True)
            time.sleep(3)
            print(f"=== {name} 捕获 {len(captured)} 个 JSON 响应 ===", flush=True)
            for i, c in enumerate(captured[:20]):
                print(f"  [{i}] {c['status']} {c['len']:>7} {c['url'][:110]}", flush=True)
            target = OUT / name
            target.mkdir(exist_ok=True)
            for i, c in enumerate(captured):
                if c.get("body"):
                    (target / f"{i:02d}.json").write_text(c["body"], encoding="utf-8")
            (target / "_index.json").write_text(
                json.dumps([{"url": c["url"], "status": c.get("status"), "len": c.get("len")}
                            for c in captured], ensure_ascii=False, indent=2), encoding="utf-8")
        page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
