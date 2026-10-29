"""按类目遍历选品库，聚合商品池（每个类目 30 条，去重合并）。

选品库单页最多 30 条且不翻页，但类目切换会换一批结果——
所以用「类目 × 30」把池子做大。

  python chanxuan_library_collect.py            # 全部类目
  python chanxuan_library_collect.py --limit 3  # 只跑前 3 个类目

产物：targets/clip/data/library_all.jsonl + library_categories.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from chanxuan_parse import parse_library_dom  # noqa: E402

URL = "https://www.chanxuan.com/library/"
OUT_DIR = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
RAW_DIR = Path(r"D:\start\ds-harness\work\_scrape\chanxuan-lib-cats")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0")


def wait_cards(page, timeout_ms=30000) -> int:
    """等商品卡片出现，返回链接数。"""
    page.wait_for_selector('a[href*="/goods/"]', state="attached", timeout=timeout_ms)
    return len(re.findall(r'href="/goods/', page.content()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个类目")
    ap.add_argument("--headless", default="true")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222", help="登录态浏览器；传空串则用无头新浏览器")
    args = ap.parse_args()
    headless = args.headless.lower() != "false"

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_dir = RAW_DIR / stamp
    raw_dir.mkdir(parents=True, exist_ok=True)

    pool: dict[str, dict] = {}
    stats = []

    with sync_playwright() as p:
        if args.cdp:
            browser = p.chromium.connect_over_cdp(args.cdp)
            ctx = browser.contexts[0]
        else:
            browser = p.chromium.launch(channel="msedge", headless=headless)
            ctx = browser.new_context(user_agent=UA, locale="zh-CN", viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        wait_cards(page)
        time.sleep(3)

        # 抓类目清单（商品类目那一行的 span.item）
        cats = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('span.el-tooltip.item').forEach(el => {
                const t = (el.innerText || '').trim();
                if (t && t.length <= 8) out.push(t);
            });
            return [...new Set(out)];
        }""")
        print(f"[info] 类目候选 {len(cats)}: {cats[:12]}{'...' if len(cats) > 12 else ''}")
        cats = [c for c in cats if c not in {"全部"}]
        if args.limit:
            cats = cats[: args.limit]

        for idx, cat in enumerate(cats, 1):
            try:
                before = re.findall(r'href="/goods/([^"]+)"', page.content())[:1]
                loc = page.locator("span.el-tooltip.item", has_text=cat).first
                loc.scroll_into_view_if_needed(timeout=5000)
                loc.click(timeout=8000)
                # 等列表真的刷新（首个商品 id 变化）
                changed = False
                for _ in range(20):
                    time.sleep(1)
                    now = re.findall(r'href="/goods/([^"]+)"', page.content())[:1]
                    if now and now != before:
                        changed = True
                        break
                time.sleep(1.5)
                n = len(re.findall(r'href="/goods/', page.content()))
                if not changed:
                    print(f"  [{idx}/{len(cats)}] {cat}: 列表未变化（可能该分类为空）")
                html = page.content()
                (raw_dir / f"{idx:02d}_{cat}.dom.html").write_text(html, encoding="utf-8")
                rows = parse_library_dom(html)
                new = 0
                for r in rows:
                    if r["product_id"] not in pool:
                        r["category"] = cat
                        pool[r["product_id"]] = r
                        new += 1
                stats.append({"category": cat, "cards": len(rows), "new": new})
                print(f"  [{idx}/{len(cats)}] {cat}: 卡片 {len(rows)}，新增 {new}，累计 {len(pool)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{idx}/{len(cats)}] {cat}: 失败 {type(exc).__name__}: {str(exc)[:80]}")
                stats.append({"category": cat, "error": type(exc).__name__})
        browser.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUT_DIR / "library_all.jsonl"
    with dst.open("w", encoding="utf-8") as fh:
        for r in pool.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (OUT_DIR / "library_categories.json").write_text(json.dumps(
        {"at": datetime.now(timezone.utc).isoformat(), "categories": stats,
         "total_unique": len(pool)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] 去重后 {len(pool)} 个商品 -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
