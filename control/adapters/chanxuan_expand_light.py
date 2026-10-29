"""轻量扩池：类目 × 排序 逐个点击，点完就收 ID，不做变化检测。

  python chanxuan_expand_light.py --target 1000 [--cats 3] [--sorts 8]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

DATA = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
CDP = "http://127.0.0.1:9222"
IDS_RE = re.compile(r'href="/goods/([A-Za-z0-9_\-+=]+)"')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--cats", type=int, default=0)
    ap.add_argument("--sorts", type=int, default=0)
    args = ap.parse_args()

    pool: dict[str, dict] = {}
    lib = DATA / "library_all.jsonl"
    if lib.exists():
        for line in lib.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                pool[r["product_id"]] = {"discovered_from": "library", "category": r.get("category")}
    pid_file = DATA / "product_ids.json"
    if pid_file.exists():
        for k, v in json.loads(pid_file.read_text(encoding="utf-8")).get("ids", {}).items():
            pool.setdefault(k, v)
    print(f"[info] 起始 {len(pool)} 个 ID，目标 {args.target}", flush=True)

    stats = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        page = ctx.new_page()
        page.goto("https://www.chanxuan.com/library/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('a[href*="/goods/"]', state="attached", timeout=40000)
        time.sleep(3)

        cats = page.evaluate("""() => [...document.querySelectorAll('span.el-tooltip.item')]
            .map(e => (e.innerText||'').trim()).filter(t => t && t.length <= 8 && t !== '全部')""")
        sorts = page.evaluate("""() => [...document.querySelectorAll('.sort-box-block .item')]
            .map(e => (e.innerText||'').trim()).filter(Boolean)""")
        if args.cats:
            cats = cats[: args.cats]
        if args.sorts:
            sorts = sorts[: args.sorts]
        print(f"[info] 类目 {len(cats)} × 排序 {len(sorts)}", flush=True)
        print(f"[info] 排序项: {sorts}", flush=True)

        for ci, cat in enumerate(cats, 1):
            if len(pool) >= args.target:
                break
            try:
                page.locator("span.el-tooltip.item", has_text=cat).first.click(timeout=8000)
                time.sleep(3.0)
            except Exception as exc:  # noqa: BLE001
                print(f"  类目[{cat}] 点击失败 {type(exc).__name__}", flush=True)
                continue
            for sort in sorts:
                if len(pool) >= args.target:
                    break
                try:
                    page.locator(".sort-box-block .item", has_text=sort).first.click(timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(3.0)
                found = set(IDS_RE.findall(page.content()))
                new = 0
                for pid in found:
                    if pid not in pool:
                        pool[pid] = {"discovered_from": f"{cat}/{sort}"}
                        new += 1
                stats.append({"category": cat, "sort": sort, "cards": len(found), "new": new})
                print(f"  [{ci}/{len(cats)}] {cat[:6]:<7} {sort:<8} 卡片{len(found):>3} 新增{new:>3} 累计{len(pool)}", flush=True)
            if len(pool) >= args.target:
                break
        page.close()

    dst = DATA / "product_ids.json"
    dst.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "total": len(pool),
                               "stats": stats, "ids": pool}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(pool)} 个 ID -> {dst}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
