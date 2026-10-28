"""chanxuan 榜单采集（公开页面，不需要登录态）。

用 Playwright 驱动本机 Edge（headless）打开榜单页，等表格渲染完，存 DOM。
后续由 chanxuan_parse.py 做确定性解析。

  python chanxuan_collect.py                # 抓默认两个榜单
  python chanxuan_collect.py --url <url>    # 抓指定页

产物：work/_scrape/chanxuan-collect/<时间戳>/<name>.dom.html
"""
from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT_ROOT = Path(r"D:\start\ds-harness\work\_scrape\chanxuan-collect")
PAGES = [
    ("clip-product", "https://www.chanxuan.com/rising-rank/clip-product.html"),
    ("clip-author", "https://www.chanxuan.com/rising-rank/clip-author.html"),
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pages = PAGES if not args.url else [(f"custom{i}", u) for i, u in enumerate(args.url)]
    out_dir = Path(args.out) if args.out else OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(user_agent=UA, locale="zh-CN", viewport={"width": 1600, "height": 1000})
        for name, url in pages:
            page = ctx.new_page()
            t0 = time.time()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                # 等表格里出现数据行
                page.wait_for_selector("table tr td", timeout=30000)
                time.sleep(3)  # 让图表/懒加载列补齐
                html = page.content()
                rows = len(re.findall(r"<td", html))
                dst = out_dir / f"{name}.dom.html"
                dst.write_text(html, encoding="utf-8")
                print(f"[ok] {name}: {len(html)/1024:.0f} KB, {rows} 个 <td>, {time.time()-t0:.1f}s -> {dst}")
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {name}: {type(exc).__name__}: {exc}")
            finally:
                page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
