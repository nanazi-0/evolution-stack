"""用已保存的各类目 DOM 重建商品池（含新增字段）。

  python rebuild_pool.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from chanxuan_parse import parse_library_dom  # noqa: E402

RAW = Path(r"D:\start\ds-harness\work\_scrape\chanxuan-lib-cats")
OUT = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
FIELDS = ["product_id", "title", "price", "earn_per_order", "commission_rate",
          "creators_count", "channel", "channel_share", "sales_yesterday",
          "sales_7d", "sales_30d", "shop", "merchant_score"]


def main() -> int:
    run = sorted(RAW.glob("*/"))[-1]
    pool: dict[str, dict] = {}
    files = 0
    for f in sorted(run.glob("*.dom.html")):
        cat = f.stem.split("_", 1)[-1].replace(".dom", "")
        rows = parse_library_dom(f.read_text(encoding="utf-8", errors="replace"))
        files += 1
        for r in rows:
            if r["product_id"] not in pool:
                r["category"] = cat
                pool[r["product_id"]] = r
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "library_all.jsonl"
    with dst.open("w", encoding="utf-8") as fh:
        for r in pool.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n = len(pool) or 1
    print(f"[ok] {files} 个类目文件 -> {len(pool)} 个商品")
    for k in FIELDS:
        ok = sum(1 for r in pool.values() if r.get(k) not in (None, "", []))
        print(f"   {k:<18}{ok/n:>6.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
