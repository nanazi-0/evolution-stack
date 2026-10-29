"""用已登录的 Camoufox profile 扩池：类目 × 排序。

  python chanxuan_camoufox_expand.py --target 1100

产物：targets/clip/data/product_ids.json（合并）
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from camoufox.sync_api import Camoufox

DATA = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
PROFILE = r"D:\start\ds-harness\work\_camoufox-profile"
URL = "https://www.chanxuan.com/library/"
ID_RE = re.compile(r'/goods/([A-Za-z0-9_\-+=]+)')


def snap(page):
    return set(ID_RE.findall(page.content()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1100)
    ap.add_argument("--headless", default="true")
    ap.add_argument("--max-combos", type=int, default=0)
    args = ap.parse_args()
    headless = args.headless.lower() != "false"

    pool: dict[str, dict] = {}
    src = DATA / "product_ids.json"
    if src.exists():
        pool = json.loads(src.read_text(encoding="utf-8")).get("ids", {})
    print(f"[info] 起始 {len(pool)} 个 ID，目标 {args.target}", flush=True)

    stats = []
    with Camoufox(headless=headless, persistent_context=True, user_data_dir=PROFILE,
                  humanize=True, os="windows", locale="zh-CN") as browser:
        page = browser.pages[0] if getattr(browser, "pages", None) else browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        time.sleep(6)

        cats = page.evaluate("""() => [...document.querySelectorAll('span.el-tooltip.item')]
            .map(e => (e.innerText||'').trim()).filter(t => t && t.length <= 8 && t !== '全部')""")
        sorts = page.evaluate("""() => [...document.querySelectorAll('.sort-box-block .item')]
            .map(e => (e.innerText||'').trim()).filter(Boolean)""")
        print(f"[info] 类目 {len(cats)} 个 / 排序 {len(sorts)} 种: {sorts}", flush=True)

        base = snap(page)
        print(f"[info] 初始页面商品 {len(base)} 个", flush=True)

        combos = 0
        for ci, cat in enumerate(cats, 1):
            if len(pool) >= args.target:
                break
            try:
                page.locator("span.el-tooltip.item", has_text=cat).first.click(timeout=8000)
                time.sleep(3)
            except Exception as exc:  # noqa: BLE001
                print(f"  类目[{cat}] 点击失败 {type(exc).__name__}", flush=True)
                continue
            for sort in sorts:
                if len(pool) >= args.target:
                    break
                combos += 1
                if args.max_combos and combos > args.max_combos:
                    break
                try:
                    page.locator(".sort-box-block .item", has_text=sort).first.click(timeout=8000)
                    time.sleep(3)
                    found = snap(page)
                    new = 0
                    for pid in found:
                        if pid not in pool:
                            pool[pid] = {"discovered_from": f"camoufox:{cat}/{sort}"}
                            new += 1
                    stats.append({"category": cat, "sort": sort, "cards": len(found), "new": new})
                    print(f"  [{ci}/{len(cats)}] {cat[:6]:<7} {sort:<8} 卡片{len(found):>3} 新增{new:>3} 累计{len(pool)}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {cat}/{sort} 失败 {type(exc).__name__}", flush=True)

    dst = DATA / "product_ids.json"
    dst.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "total": len(pool),
                               "stats_camoufox_expand": stats, "ids": pool},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {len(pool)} 个 ID -> {dst}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
