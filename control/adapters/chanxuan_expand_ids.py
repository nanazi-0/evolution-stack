"""扩展商品 ID 池：类目 × 排序 组合，每个组合拿 30 条。

不保存 HTML，只收 ID（快）。够 target 就停。

  python chanxuan_expand_ids.py --target 1000
产物：targets/clip/data/product_ids.json （含每个 ID 的发现来源）
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


def snapshot(page) -> str:
    """整页 ID 集合的指纹——比"首个 ID"可靠（列表可能是追加式的）。"""
    ids = sorted(set(IDS_RE.findall(page.content())))
    return f"{len(ids)}:" + ",".join(ids[:5])


def wait_change(page, before: str, timeout=12) -> bool:
    for _ in range(int(timeout * 2)):
        time.sleep(0.5)
        if snapshot(page) != before:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--max-combos", type=int, default=0)
    args = ap.parse_args()

    pool: dict[str, dict] = {}
    # 先并入已有池子
    lib = DATA / "library_all.jsonl"
    if lib.exists():
        for line in lib.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                pool[r["product_id"]] = {"discovered_from": "library", "category": r.get("category")}
    print(f"[info] 已有 {len(pool)} 个 ID，目标 {args.target}")

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
        print(f"[info] 类目 {len(cats)} × 排序 {len(sorts)} = {len(cats)*len(sorts)} 组合")

        combos = 0
        for ci, cat in enumerate(cats, 1):
            try:
                before = snapshot(page)
                page.locator("span.el-tooltip.item", has_text=cat).first.click(timeout=8000)
                if not wait_change(page, before):
                    print(f"  类目[{cat}] 列表未变化（继续试排序）")
            except Exception as exc:  # noqa: BLE001
                print(f"  类目[{cat}] 点击失败 {type(exc).__name__}")
                continue

            for sort in sorts:
                if len(pool) >= args.target:
                    break
                combos += 1
                if args.max_combos and combos > args.max_combos:
                    break
                try:
                    before = snapshot(page)
                    page.locator(".sort-box-block .item", has_text=sort).first.click(timeout=8000)
                    wait_change(page, before)
                    time.sleep(1.0)
                    found = set(IDS_RE.findall(page.content()))
                    new = 0
                    for pid in found:
                        if pid not in pool:
                            pool[pid] = {"discovered_from": f"{cat}/{sort}"}
                            new += 1
                    stats.append({"category": cat, "sort": sort, "cards": len(found), "new": new})
                    print(f"  [{ci}/{len(cats)}] {cat[:6]:<8} {sort:<8} 卡片{len(found):>3} 新增{new:>3} 累计{len(pool)}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {cat}/{sort} 失败 {type(exc).__name__}")
            if len(pool) >= args.target:
                break
        page.close()

    if len(pool) < args.target:
        # 兜底：并入其他榜单页面里发现的 ID
        for run in sorted(Path(r"D:\start\ds-harness\work\_capture\chanxuan-routes").glob("*/"))[-1:]:
            for f in run.glob("items/*.json"):
                d = json.loads(f.read_text(encoding="utf-8"))
                for ln in d.get("links", []):
                    m = re.search(r"/goods/([A-Za-z0-9_\-+=]+)\.html", ln.get("href") or "")
                    if m and m.group(1) not in pool:
                        pool[m.group(1)] = {"discovered_from": f"route:{d.get('route')}"}
        print(f"[info] 榜单/路由页面兜底后 {len(pool)} 个")

    dst = DATA / "product_ids.json"
    dst.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                               "total": len(pool), "stats": stats,
                               "ids": pool}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(pool)} 个商品 ID -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
