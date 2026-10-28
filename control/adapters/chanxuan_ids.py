"""从榜单里点出商品详情 URL（榜单 DOM 不含链接，但点击会跳转）。

做法：无头 Edge 打开商品榜 → 逐行点"详情" → 抓跳转后的 /goods/<id>.html → 返回列表。
输出 rank -> product_url 映射，供后续补详情层。

  python chanxuan_ids.py --limit 3      # 先试 3 行
  python chanxuan_ids.py                # 全量 20 行

产物：work/_scrape/chanxuan-collect/<ts>/product_urls.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.chanxuan.com/rising-rank/clip-product.html"
OUT_ROOT = Path(r"D:\start\ds-harness\work\_scrape\chanxuan-collect")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0")
GOODS = re.compile(r"/goods/([A-Za-z0-9_\-+=]+)\.html")


def extract_ids(page) -> list[dict]:
    """点每一行的详情，记录跳转 URL。"""
    found: list[dict] = []
    rows = page.query_selector_all("table tbody tr")
    data_rows = [tr for tr in rows if len(tr.query_selector_all("td")) >= 8]
    print(f"[info] 数据行 {len(data_rows)}")
    return found  # 占位，实际逻辑在 main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(user_agent=UA, locale="zh-CN", viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector("table tbody tr td", timeout=30000)
        time.sleep(3)

        total = len([tr for tr in page.query_selector_all("table tbody tr")
                     if len(tr.query_selector_all("td")) >= 8])
        limit = args.limit or total
        print(f"[info] 待处理 {min(limit, total)} / {total} 行")

        for i in range(min(limit, total)):
            # 每次重新定位，避免 DOM 更新后失效
            rows = [tr for tr in page.query_selector_all("table tbody tr")
                    if len(tr.query_selector_all("td")) >= 8]
            if i >= len(rows):
                break
            row = rows[i]
            tds = row.query_selector_all("td")
            title = re.sub(r"\s+", " ", tds[1].inner_text()).strip()[:40]
            buttons = row.query_selector_all("td:last-child button, td:last-child a, td:last-child span")
            clicked = False
            url = None
            for btn in buttons:
                txt = (btn.inner_text() or "").strip()
                if txt not in {"详情", "查看趋势图", ""}:
                    continue
                if txt == "查看趋势图":
                    continue
                try:
                    with ctx.expect_page(timeout=6000) as popup_info:
                        btn.click()
                    popup = popup_info.value
                    popup.wait_for_load_state("domcontentloaded", timeout=15000)
                    url = popup.url
                    popup.close()
                except Exception:
                    try:
                        btn.click(timeout=5000)
                        time.sleep(1.5)
                        url = page.url
                        if "goods" in url:
                            page.go_back(wait_until="domcontentloaded", timeout=15000)
                            page.wait_for_selector("table tbody tr td", timeout=20000)
                            time.sleep(1)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [warn] 行{i+1} 点击失败: {type(exc).__name__}")
                clicked = True
                break
            m = GOODS.search(url) if url else None
            item = {"rank": i + 1, "title": title,
                    "product_id": m.group(1) if m else None,
                    "product_url": url if m else None}
            result.append(item)
            print(f"  [{i+1:>2}] {'OK ' if m else 'MISS'} {title[:24]} -> {(m.group(1) if m else url)[:40] if (m or url) else ''}")
            if not clicked:
                print(f"       (没找到可点的详情按钮)")
        page.close()
        browser.close()

    dst = out_dir / "product_urls.json"
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in result if r["product_id"])
    print(f"[done] {ok}/{len(result)} 拿到 product_id -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
