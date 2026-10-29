"""用 Camoufox（已登录 profile）采集商品详情页，输出与 chanxuan_capture 相同格式。

Edge 侧数据被限流（页面壳能开但数值全是 "--"），Camoufox 不受影响。

  python chanxuan_capture_camoufox.py [--limit N]
产物：work/_capture/chanxuan-camoufox/<时间戳>/{raw,items,manifest.jsonl}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from camoufox.sync_api import Camoufox

sys.path.insert(0, str(Path(__file__).parent))
from chanxuan_capture import CLICKABLE_JS, DETAIL_JS, LINKS_JS, extract_fields  # noqa: E402

DATA = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
OUT_ROOT = Path(r"D:\start\ds-harness\work\_capture\chanxuan-camoufox")
PROFILE = r"D:\start\ds-harness\work\_camoufox-profile"


def slug(url: str) -> str:
    m = re.search(r"/goods/([A-Za-z0-9_\-+=]+)\.html", url)
    return m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume-from", nargs="*", default=[], help="跳过这些目录里已采的 page_id")
    ap.add_argument("--headless", default="true")
    args = ap.parse_args()
    headless = args.headless.lower() != "false"

    ids_map = json.loads((DATA / "product_ids.json").read_text(encoding="utf-8")).get("ids", {})
    done: set[str] = set()
    for d in args.resume_from:
        done |= {f.stem for f in Path(d, "items").glob("*.json")}
    todo = [pid for pid in ids_map if pid not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"[info] 总 {len(ids_map)} 个，已采 {len(done)}，待采 {len(todo)}", flush=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / stamp
    raw_dir, item_dir = out / "raw", out / "items"
    raw_dir.mkdir(parents=True, exist_ok=True)
    item_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    t0 = time.time()
    with Camoufox(headless=headless, persistent_context=True, user_data_dir=PROFILE,
                  humanize=True, os="windows", locale="zh-CN") as browser:
        page = browser.pages[0] if getattr(browser, "pages", None) else browser.new_page()
        for i, pid in enumerate(todo, 1):
            url = f"https://www.chanxuan.com/goods/{pid}.html"
            try:
                html = ""
                full_text = ""
                detail_text = ""
                fields, missing = {}, []
                # 数据接口会间歇性返回空壳（字段很少），重试到拿到真数据或超次数
                for attempt in range(1, 4):
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    time.sleep(3.0 + attempt * 1.5)
                    html = page.content()
                    full_text = page.inner_text("body")
                    try:
                        detail_text = page.evaluate(DETAIL_JS) or ""
                    except Exception:  # noqa: BLE001
                        detail_text = ""
                    text = detail_text or full_text
                    fields, missing = extract_fields(text)
                    if len(fields) >= 6:
                        break
                    if attempt < 3:
                        time.sleep(2 * attempt)
                raw_b = html.encode("utf-8")
                (raw_dir / f"{pid}.html").write_bytes(raw_b)
                (raw_dir / f"{pid}.txt").write_bytes(full_text.encode("utf-8"))
                (raw_dir / f"{pid}.detail.txt").write_bytes(detail_text.encode("utf-8"))
                sha = hashlib.sha256(raw_b).hexdigest()
                links = page.evaluate(LINKS_JS)
                controls = page.evaluate(CLICKABLE_JS)
                item = {
                    "page_id": pid, "source_url": url,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "raw_html": str(raw_dir / f"{pid}.html"),
                    "raw_text": str(raw_dir / f"{pid}.txt"),
                    "raw_sha256": sha, "raw_bytes": len(raw_b),
                    "title": (page.title() or "").strip(),
                    "fields": fields, "missing": missing,
                    "links": links, "controls": controls,
                    "counts": {"links": len(links), "controls": len(controls), "fields_found": len(fields)},
                    "captured_by": "camoufox",
                    "data_status": "ok" if len(fields) >= 6 else "empty-or-delisted",
                }
                (item_dir / f"{pid}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
                ok += 1
                if i % 5 == 0 or i <= 3:
                    print(f"  [{i}/{len(todo)}] OK fields={len(fields)} links={len(links)} "
                          f"{len(raw_b)//1024}KB {pid[:14]}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(todo)}] FAIL {pid[:14]}: {type(exc).__name__}: {str(exc)[:60]}", flush=True)

    with (out / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for f in sorted(item_dir.glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            fh.write(json.dumps({k: d[k] for k in ("page_id", "source_url", "captured_at",
                                                   "raw_html", "raw_sha256", "counts")},
                                ensure_ascii=False) + "\n")
    print(f"\n[done] {ok}/{len(todo)} 页，耗时 {(time.time()-t0)/60:.1f} 分钟 -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
