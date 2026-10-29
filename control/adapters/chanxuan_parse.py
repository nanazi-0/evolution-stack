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

CN_UNITS = {"w": 10_000, "W": 10_000, "万": 10_000, "亿": 100_000_000, "k": 1_000, "K": 1_000, "千": 1_000}


def parse_num(text: str):
    """'4,337' -> 4337 ; '16.6w' -> 166000 ; '5000-7500' -> [5000,7500] ; '-' -> None"""
    if text is None:
        return None
    s = text.strip().replace(",", "").replace(" ", "")
    if not s or s in {"-", "—", "/"}:
        return None
    # 区间
    m = re.fullmatch(r"([0-9.]+)([wW万kK亿千]?)[-~]([0-9.]+)([wW万kK亿千]?)", s)
    if m:
        lo_unit = CN_UNITS.get(m.group(2), 1)
        hi_unit = CN_UNITS.get(m.group(4), 1)
        return [float(m.group(1)) * lo_unit, float(m.group(3)) * hi_unit]
    # 单值
    m = re.fullmatch(r"([0-9.]+)([wW万kK亿千]?)\+?", s)
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


def parse_library_dom(html: str) -> list[dict]:
    """选品库卡片：容器 div 带 id，内含 <a href="/goods/<id>.html">标题</a>。
    这是唯一能同时拿到 product_id 与业务字段的公开页面。"""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for div in soup.find_all("div", attrs={"id": True}):
        pid = div.get("id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", pid):
            continue
        a = div.find("a", href=re.compile(r"/goods/"))
        if a is None:
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        txt = re.sub(r"\s+", " ", div.get_text(" ", strip=True))

        # 卡片文本形如：
        # 标题 [榜单标签] ¥19.90 赚 ￥4.37 50-100人在带 直播 100% 蝉选高佣 22 % 投广佣金 5 %
        # 公开佣金 5% 5千-1万 昨日 2.5万-5万 7日销量 2.5万-5万 30日销量 奥力给官方旗舰店 综合体验 5
        RANGE = r"([0-9.]+[万千kK]?[-~][0-9.]+[万千kK]?|[0-9.]+[万千kK]?\+?)"

        def grab(pattern, cast=float, flags=0):
            m = re.search(pattern, txt, flags)
            return cast(m.group(1)) if m else None

        price = grab(r"[¥￥]\s*([0-9.]+)")
        earn = grab(r"赚\s*[¥￥]?\s*([0-9.]+)")
        creators = parse_num(re.search(r"([0-9.]+[万千kK]?[-~][0-9.]+[万千kK]?|[0-9.]+[万千kK]?)\s*人在带", txt).group(1)) if re.search(r"(?:[0-9.]+[万千kK]?[-~][0-9.]+[万千kK]?|[0-9.]+[万千kK]?)\s*人在带", txt) else None
        ch_m = re.search(r"(直播|视频|商品卡)\s*([0-9.]+)\s*%", txt)
        comm_m = re.search(r"蝉选高佣\s*([0-9.]+)\s*%", txt)
        public_m = re.search(r"公开佣金\s*([0-9.]+)\s*%", txt)
        ad_m = re.search(r"投广佣金\s*([0-9.]+)\s*%", txt)
        s_y = parse_num(re.search(RANGE + r"\s*昨日", txt).group(1)) if re.search(RANGE + r"\s*昨日", txt) else None
        s_7 = parse_num(re.search(RANGE + r"\s*7日销量", txt).group(1)) if re.search(RANGE + r"\s*7日销量", txt) else None
        s_30 = parse_num(re.search(RANGE + r"\s*30日销量", txt).group(1)) if re.search(RANGE + r"\s*30日销量", txt) else None
        shop_m = re.search(r"30日销量\s*([\u4e00-\u9fa5A-Za-z0-9()（）·\-]+?店)\s*综合体验", txt)
        score_m = re.search(r"综合体验\s*([0-9.]+)", txt)
        out.append({
            "kind": "product",
            "product_id": pid,
            "product_url": f"https://www.chanxuan.com{a.get('href')}",
            "title": title,
            "price": price,
            "earn_per_order": earn,
            "commission_rate": round(float(comm_m.group(1)) / 100, 4) if comm_m else None,
            "public_commission": round(float(public_m.group(1)) / 100, 4) if public_m else None,
            "ad_commission": round(float(ad_m.group(1)) / 100, 4) if ad_m else None,
            "creators_count": creators,
            "channel": ch_m.group(1) if ch_m else None,
            "channel_share": round(float(ch_m.group(2)) / 100, 4) if ch_m else None,
            "sales_yesterday": s_y,
            "sales_7d": s_7,
            "sales_30d": s_30,
            "shop": shop_m.group(1) if shop_m else None,
            "merchant_score": float(score_m.group(1)) if score_m else None,
            "source": "library",
        })
    return out


def match_ids(rank_products: list[dict], lib_products: list[dict]) -> tuple[list[dict], dict]:
    """按标题精确匹配，给榜单商品补 product_id。"""
    by_title = {}
    for p in lib_products:
        by_title.setdefault(p["title"].strip(), p)
    matched = 0
    for r in rank_products:
        lib = by_title.get(r["title"].strip())
        if lib:
            r["product_id"] = lib["product_id"]
            r["product_url"] = lib["product_url"]
            r["merchant_score"] = lib.get("merchant_score")
            matched += 1
        else:
            r["product_id"] = None
            r["product_url"] = None
    rate = round(matched / (len(rank_products) or 1), 3)
    return rank_products, {"matched": matched, "total": len(rank_products), "rate": rate}


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
        "library": next(dom_dir.glob("*library*.dom.html"), None) or next(dom_dir.glob("custom0.dom.html"), None),
    }
    meta = {"parsed_at": datetime.now(timezone.utc).isoformat(), "sources": {}, "coverage": {}}
    total = 0
    for kind, f in files.items():
        if f is None:
            print(f"[skip] 找不到 {kind} 的 DOM")
            continue
        html = f.read_text(encoding="utf-8", errors="replace")
        if kind == "author":
            rows = parse_author_dom(html)
        elif kind == "product":
            rows = parse_product_dom(html)
        else:
            rows = parse_library_dom(html)
        dst = out_dir / (f"{kind}s.jsonl" if kind != "library" else "library_products.jsonl")
        with dst.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        if kind == "author":
            fields = ["name", "douyin_id", "followers", "settle_amount", "video_sales",
                      "aov", "active_products", "sale_videos"]
        elif kind == "product":
            fields = ["title", "price", "commission_rate", "settle_amount", "sales",
                      "related_videos", "related_creators", "creator", "shop"]
        else:
            fields = ["product_id", "title", "price", "commission_rate", "sales_7d",
                      "sales_30d", "shop", "merchant_score"]
        cov = coverage(rows, fields)
        meta["sources"][kind] = str(f)
        meta["coverage"][kind] = {"rows": len(rows), "fields": cov}
        total += len(rows)
        print(f"[ok] {kind}: {len(rows)} 行 -> {dst}")
        for k, v in cov.items():
            flag = "" if v >= 0.99 else "  <-- 缺"
            print(f"     {k:<18} {v:.0%}{flag}")

    # 榜单商品 ↔ 选品库按标题匹配，补 product_id
    rank_f = out_dir / "products.jsonl"
    lib_f = out_dir / "library_products.jsonl"
    if rank_f.exists() and lib_f.exists():
        rank = [json.loads(x) for x in rank_f.read_text(encoding="utf-8").splitlines() if x.strip()]
        lib = [json.loads(x) for x in lib_f.read_text(encoding="utf-8").splitlines() if x.strip()]
        rank, res = match_ids(rank, lib)
        dst = out_dir / "products_with_id.jsonl"
        with dst.open("w", encoding="utf-8") as fh:
            for r in rank:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        meta["match"] = res
        print(f"[match] 榜单商品补 ID: {res['matched']}/{res['total']} ({res['rate']:.0%}) -> {dst}")

    (out_dir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] 共 {total} 行，元数据 {out_dir / '_meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
