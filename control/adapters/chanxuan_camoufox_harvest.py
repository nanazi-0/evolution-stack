"""用 Camoufox（StealthyFetcher）遍历公开列表页，挖商品 ID。

这些页面游客可见、不需要登录，且 Camoufox 的 Firefox 指纹绕过了对 Edge 的软封。

  python chanxuan_camoufox_harvest.py
产物：targets/clip/data/product_ids.json（与已有池子合并）
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from scrapling.fetchers import StealthyFetcher

DATA = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
RAW = Path(r"D:\start\ds-harness\work\_capture\chanxuan-camoufox")
GOODS = re.compile(r'/goods/([A-Za-z0-9_\-+=]+)')

PAGES = [
    ("library", "https://www.chanxuan.com/library/"),
    ("hotList", "https://www.chanxuan.com/hotList"),
    ("hotList-gmv", "https://www.chanxuan.com/hotList/gmv-rank"),
    ("hotList-sales", "https://www.chanxuan.com/hotList/sales-rank"),
    ("hotColumn", "https://www.chanxuan.com/hotColumn"),
    ("head-column", "https://www.chanxuan.com/head-column"),
    ("shops", "https://www.chanxuan.com/shops"),
    ("commission", "https://www.chanxuan.com/commission"),
    ("collection", "https://www.chanxuan.com/collection/goods"),
    ("high-comm", "https://www.chanxuan.com/high-commission/polymerization"),
    ("videoClip", "https://www.chanxuan.com/videoClip/"),
    ("rank-product", "https://www.chanxuan.com/rising-rank/clip-product.html"),
    ("rank-author", "https://www.chanxuan.com/rising-rank/clip-author.html"),
    ("rank-material", "https://www.chanxuan.com/rising-rank/clip-material.html"),
    ("rank-coupon", "https://www.chanxuan.com/rising-rank/couponTop.html"),
    ("rank-cx", "https://www.chanxuan.com/rising-rank/cxTop.html"),
    ("rank-sp", "https://www.chanxuan.com/rising-rank/spTop.html"),
    ("rank-tw", "https://www.chanxuan.com/rising-rank/twTop.html"),
    ("rank-zb", "https://www.chanxuan.com/rising-rank/zbTop.html"),
]


def main() -> int:
    pool: dict[str, dict] = {}
    src = DATA / "product_ids.json"
    if src.exists():
        pool = json.loads(src.read_text(encoding="utf-8")).get("ids", {})
    print(f"[info] 起始 {len(pool)} 个 ID", flush=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RAW / stamp
    out.mkdir(parents=True, exist_ok=True)

    stats = []
    for name, url in PAGES:
        try:
            t0 = time.time()
            r = StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=90000)
            html = getattr(r, "html_content", "") or ""
            (out / f"{name}.html").write_bytes(html.encode("utf-8"))
            found = set(GOODS.findall(html))
            new = [p for p in found if p not in pool]
            for p in new:
                pool[p] = {"discovered_from": f"camoufox:{name}"}
            stats.append({"page": name, "goods": len(found), "new": len(new)})
            print(f"  [{name:<14}] {len(html)//1024:>4} KB  goods={len(found):>3} 新增={len(new):>3} 累计={len(pool)} "
                  f"({time.time()-t0:.0f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{name:<14}] 失败 {type(exc).__name__}: {str(exc)[:80]}", flush=True)
            stats.append({"page": name, "error": type(exc).__name__})

    dst = DATA / "product_ids.json"
    dst.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "total": len(pool),
                               "stats_camoufox": stats, "ids": pool},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {len(pool)} 个 ID -> {dst}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
