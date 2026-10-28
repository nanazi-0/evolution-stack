"""chanxuan 榜单 DOM → 结构化 JSONL（确定性解析，不用模型）。

为什么走 DOM 而不是 API：
  实测 /v1/product/rank/clip 返回的 data 是加密串（base64 密文），
  解密在前端 JS 里做。直连 API 拿不到可读数据，所以解析渲染后的 DOM。

用法：
  python chanxuan_parse.py                          # 用默认抓好的 DOM
  python chanxuan_parse.py --dom <文件> --kind author

输出：
  <project>/data/authors.jsonl
  <project>/data/products.jsonl
每行一条，字段固定；解析覆盖率写进 meta。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

DEFAULT_DIR = Path(r"D:\start\ds-harness\work\_scrape\chanxuan-p1")
OUT_ROOT = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")

CN_UNITS = {"w": 10_000, "W": 10_000, "万": 10_000, "亿": 100_000_000, "k": 1_000, "K": 1_000}


def parse_num(text: str):
    """'4,337' -> 4337 ; '16.6w' -> 166000 ; '5000-7500' -> [5000,7500] ; '-' -> None"""
    if text is None:
        return None
    s = text.strip().replace(",", "").replace(" ", "")
    if not s or s in {"-", "—", "/"}:
        return None
    # 区间
    m = re.fullmatch(r"([0-9.]+)([wW万kK亿]?)[-~]([0-9.]+)([wW万kK亿]?)", s)
    if m:
        lo_unit = CN_UNITS.get(m.group(2), 1)
        hi_unit = CN_UNITS.get(m.group(4), 1)
        return [float(m.group(1)) * lo_unit, float(m.group(3)) * hi_unit]
    # 单值
    m = re.fullmatch(r"([0-9.]+)([wW万kK亿]?)\+?", s)
    if m:
        return float(m.group(1)) * CN_UNITS.get(m.group(2), 1)
    return None


def parse_percent(text: str):
    if not text:
        return None
    m = re.search(r"([0-9.]+)\s*%", text)
    return round(float(m.group(1)) / 100, 4) if m else None


def cell_text(td) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()


def parse_author_dom(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        cells = [cell_text(td) for td in tds]
        info = cells[1]
        m_id = re.search(r"抖音号[:：]\s*(\S+)", info)
        name = re.sub(r"抖音号[:：].*$", "", info).strip()
        rows.append({
            "kind": "author",
            "rank": int(parse_num(cells[0]) or 0),
            "name": name,
            "douyin_id": m_id.group(1) if m_id else None,
            "followers": parse_num(cells[2]),
            "settle_amount": parse_num(cells[3]),
            "video_sales": parse_num(cells[4]),
            "aov": parse_num(cells[5]),
            "active_products": parse_num(cells[6]),
            "sale_videos": parse_num(cells[7]),
            "source": "rising-rank/clip-author",
        })
    return rows


def parse_product_dom(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        cells = [cell_text(td) for td in tds]
        title_cell = tds[1]
        img = title_cell.find("img")
        price_m = re.search(r"[¥￥]\s*([0-9.]+)", cells[1])
        name = re.sub(r"[¥￥]\s*[0-9.]+.*$", "", cells[1]).strip()
        creator_cell = cells[7]
        shop_m = re.search(r"小店[:：]\s*(\S+)", creator_cell)
        creator = re.sub(r"小店[:：].*$", "", creator_cell).strip()
        rows.append({
            "kind": "product",
            "rank": int(parse_num(cells[0]) or 0),
            "title": name,
            "price": float(price_m.group(1)) if price_m else None,
            "commission_rate": parse_percent(cells[2]),
            "settle_amount": parse_num(cells[3]),
            "sales": parse_num(cells[4]),
            "related_videos": parse_num(cells[5]),
            "related_creators": parse_num(cells[6]),
            "creator": creator or None,
            "shop": shop_m.group(1) if shop_m else None,
            "image": img.get("src") if img else None,
            "source": "rising-rank/clip-product",
        })
    return rows


def coverage(rows: list[dict], fields: list[str]) -> dict:
    n = len(rows) or 1
    return {f: round(sum(1 for r in rows if r.get(f) not in (None, "", [])) / n, 3) for f in fields}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dom-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--out", default=str(OUT_ROOT))
    args = ap.parse_args()

    dom_dir = Path(args.dom_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "author": next(dom_dir.glob("*clip-author*.dom.html"), None),
        "product": next(dom_dir.glob("*clip-product*.dom.html"), None),
    }
    meta = {"parsed_at": datetime.now(timezone.utc).isoformat(), "sources": {}, "coverage": {}}
    total = 0
    for kind, f in files.items():
        if f is None:
            print(f"[skip] 找不到 {kind} 的 DOM")
            continue
        html = f.read_text(encoding="utf-8", errors="replace")
        rows = parse_author_dom(html) if kind == "author" else parse_product_dom(html)
        dst = out_dir / f"{kind}s.jsonl"
        with dst.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fields = (["name", "douyin_id", "followers", "settle_amount", "video_sales",
                   "aov", "active_products", "sale_videos"] if kind == "author"
                  else ["title", "price", "commission_rate", "settle_amount", "sales",
                        "related_videos", "related_creators", "creator", "shop"])
        cov = coverage(rows, fields)
        meta["sources"][kind] = str(f)
        meta["coverage"][kind] = {"rows": len(rows), "fields": cov}
        total += len(rows)
        print(f"[ok] {kind}: {len(rows)} 行 -> {dst}")
        for k, v in cov.items():
            flag = "" if v >= 0.99 else "  <-- 缺"
            print(f"     {k:<18} {v:.0%}{flag}")

    (out_dir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] 共 {total} 行，元数据 {out_dir / '_meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
