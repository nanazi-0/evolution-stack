"""按路由表抓取全站页面（每类页面留一份证据）。

路由表来自 /js/app.js 的 Vue router 定义（127 条），是目前唯一权威的"全站页面清单"。

robots.txt 禁止的路径默认跳过（可用 --include-disallowed 强制）：
  /*?*  /login*  /dou/  /pallet/  /other/  /h5/  /exclusive/  /bring/  /activity/  /hand-card/detail*

  python chanxuan_route_crawl.py --list                 # 只列路由与可抓状态
  python chanxuan_route_crawl.py --limit 20             # 先抓 20 个
  python chanxuan_route_crawl.py                        # 抓全部允许的
  python chanxuan_route_crawl.py --include-disallowed   # 连禁止的一起抓（风险自担）
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from chanxuan_capture import CLICKABLE_JS, LINKS_JS, capture

BASE = "https://www.chanxuan.com"
ROUTES_FILE = Path(r"D:\start\ds-harness\work\_tmp\cx-js\routes.txt")
OUT_ROOT = Path(r"D:\start\ds-harness\work\_capture\chanxuan-routes")
CDP = "http://127.0.0.1:9222"

DISALLOWED_PREFIXES = ["/login", "/dou/", "/pallet/", "/other/", "/h5/",
                       "/exclusive/", "/bring/", "/activity/", "/hand-card/detail"]


def load_routes() -> list[str]:
    routes = [l.strip().lstrip("\ufeff") for l in ROUTES_FILE.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    return sorted(set(routes))


def classify(route: str) -> str:
    if route in {"/", "/404"}:
        return "static"
    if ":" in route:
        return "dynamic(no-id)"
    return "static"


def is_allowed(route: str) -> tuple[bool, str]:
    if "?" in route:
        return False, "robots: /*?*"
    for p in DISALLOWED_PREFIXES:
        if route == p or route.startswith(p):
            return False, f"robots: {p}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-disallowed", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    routes = load_routes()
    plan = []
    for r in routes:
        ok, why = is_allowed(r)
        kind = classify(r)
        plan.append({"route": r, "kind": kind, "allowed": ok, "reason": why})

    if args.list:
        for p in plan:
            flag = "OK " if p["allowed"] else "SKIP"
            print(f"  [{flag}] {p['kind']:<16} {p['route']}" + (f"   ({p['reason']})" if p["reason"] else ""))
        ok = sum(1 for p in plan if p["allowed"])
        print(f"\n总计 {len(plan)} 条：可抓 {ok}、rodoits 禁止 {len(plan)-ok}")
        return 0

    todo = [p for p in plan if p["allowed"] or args.include_disallowed]
    todo = [p for p in todo if p["kind"] == "static" or args.include_disallowed is False]
    # 动态路由没有实例 id，只有 --include-disallowed 时也跳过（无 id 无法打开）
    todo = [p for p in todo if ":" not in p["route"]]
    if args.limit:
        todo = todo[: args.limit]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / stamp
    raw_dir = out / "raw"
    item_dir = out / "items"
    raw_dir.mkdir(parents=True, exist_ok=True)
    item_dir.mkdir(parents=True, exist_ok=True)

    print(f"[info] 待抓 {len(todo)} 个路由 -> {out}")
    ok = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        for i, item in enumerate(todo, 1):
            url = BASE + item["route"]
            try:
                rec = capture(page, url, raw_dir, item_dir)
                rec["route"] = item["route"]
                (item_dir / f"{rec['page_id']}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                ok += 1
                print(f"  [{i}/{len(todo)}] OK  {item['route'][:42]:<44} "
                      f"links={rec['counts']['links']:<3} controls={rec['counts']['controls']:<4} "
                      f"{rec['raw_bytes']//1024}KB", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(todo)}] FAIL {item['route'][:42]}: {type(exc).__name__}: {str(exc)[:70]}", flush=True)
            time.sleep(args.delay)
        page.close()

    manifest = out / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for f in sorted(item_dir.glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            fh.write(json.dumps({"route": d.get("route"), "page_id": d["page_id"],
                                 "source_url": d["source_url"], "captured_at": d["captured_at"],
                                 "raw_sha256": d["raw_sha256"], "counts": d["counts"]},
                                ensure_ascii=False) + "\n")
    (out / "routes.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {ok}/{len(todo)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
