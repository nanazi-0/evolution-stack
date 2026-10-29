"""蝉选/蝉妈妈 页面全量留证采集。

原则：
  * **原封不动**：每个页面保存完整渲染后 HTML（原始证据），不做任何改写
  * **可回溯**：每个解析值都带 source_url / captured_at / raw_sha256 / 原文片段
  * **不捏造**：取不到就写 null 并在 missing 里说明原因，绝不用估算值填充

输出（每个页面一套）：
  <out>/raw/<page_id>.html        完整 HTML
  <out>/raw/<page_id>.txt         body 纯文本（原样，用于核对）
  <out>/items/<page_id>.json      {
        source_url, captured_at, raw_html, raw_sha256,
        fields: {字段: {value, snippet}},     # snippet = 原文里的原句
        links: [{href, text}],
        controls: [{text, tag, cls, role, aria}],
        missing: [说明]
      }
  <out>/manifest.jsonl            一行一个页面

  python chanxuan_capture.py --limit 3
  python chanxuan_capture.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

DATA = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
OUT_ROOT = Path(r"D:\start\ds-harness\work\_capture\chanxuan")
CDP = "http://127.0.0.1:9222"

# 每条记录的字段抽取规则：(字段名, 正则)。snippet 会是命中的完整片段。
FIELD_RULES = {
    "price": r"[¥￥]\s*[0-9.]+",
    "earn_per_order": r"赚\s*[¥￥]?\s*[0-9.]+",
    "commission": r"蝉选高佣\s*[0-9.]+\s*%",
    "ad_commission": r"投广佣金\s*[0-9.]+\s*%",
    "public_commission": r"公开佣金\s*[0-9.]+\s*%",
    "creators": r"[0-9.]+[万千kK]?[-~]?[0-9.万千米kK]*\s*人在带",
    "channel": r"(直播|视频|商品卡)\s*[0-9.]+\s*%",
    "sales_yesterday": r"[0-9.]+[万千kK]?[-~]?[0-9.万千米kK]*\s*昨日",
    "sales_7d": r"[0-9.]+[万千kK]?[-~]?[0-9.万千米kK]*\s*7日销量",
    "sales_30d": r"[0-9.]+[万千kK]?[-~]?[0-9.万千米kK]*\s*30日销量",
    "merchant_score": r"综合体验\s*[0-9.]+",
    "category_path": r"[\u4e00-\u9fa5]+-[\u4e00-\u9fa5\-]+",
    "stock": r"总库存\s*[0-9.]+[万千]?",
    "total_sales": r"总销量\s*[0-9.]+[万千]?",
    "activity_expiry": r"活动到期时间[^\n]{0,30}",
}

CLICKABLE_JS = """() => {
  const out = [];
  const seen = new Set();
  const push = (el, kind) => {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
    if (!text || text.length > 40) return;
    const key = kind + '|' + text;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      text,
      kind,
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 120),
      role: el.getAttribute('role'),
      aria: el.getAttribute('aria-label'),
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
    });
  };
  document.querySelectorAll('button').forEach(e => push(e, 'button'));
  document.querySelectorAll('[role="button"]').forEach(e => push(e, 'role=button'));
  document.querySelectorAll('.el-tooltip.item').forEach(e => push(e, 'filter-item'));
  document.querySelectorAll('.cursor-pointer').forEach(e => push(e, 'cursor-pointer'));
  return out;
}"""

DETAIL_JS = """() => {
  const want = ['到手价', '总库存', '佣金'];
  let best = null;
  document.querySelectorAll('div').forEach(el => {
    const t = el.innerText || '';
    if (t.length < 80) return;
    if (!want.every(w => t.includes(w))) return;
    if (!best || t.length < (best.innerText || '').length) best = el;
  });
  return best ? best.innerText : '';
}"""

LINKS_JS = """() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    const href = a.getAttribute('href');
    const text = (a.innerText || a.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    const key = href + '|' + text;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({href, text, target: a.getAttribute('target')});
  });
  return out;
}"""


def slug_from_url(url: str) -> str:
    m = re.search(r"/goods/([A-Za-z0-9_\-+=]+)\.html", url)
    if m:
        return m.group(1)
    m = re.search(r"chanxuan\.com/([^?#]+)", url)
    return re.sub(r"[^A-Za-z0-9]+", "_", m.group(1)).strip("_") if m else hashlib.md5(url.encode()).hexdigest()[:12]


VALUE_RE = r"[0-9][0-9.,]*\s*[万千wWkK%]*|[0-9.]+-[0-9.]+\s*[万千wW]?|[¥￥]\s*[0-9.]+"

# 标签 -> (取值模式, 最多向后看几行)
LABEL_RULES = {
    "total_sales": ("总销量", 3),
    "stock": ("总库存", 3),
    "price": ("到手价", 4),
    "daily_price": ("日常价", 3),
    "category_path": ("类目", 3),
    "activity_expiry": ("活动到期时间", 2),
    "merchant_score": ("商家体验分", 3),
    "product_exp": ("商品体验", 3),
    "service_exp": ("商家服务", 3),
    "logistics_exp": ("物流体验", 3),
    "commission": ("蝉选高佣", 3),
    "ad_commission": ("投广佣金", 3),
    "public_commission": ("公开佣金", 3),
    "earn_per_order": ("预估每单赚", 4),
    "sales_7d": ("商品销量", 5),
    "creators": ("推广达人数", 4),
    "order_rate": ("出单率", 4),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_fields(text: str) -> tuple[dict, list[str]]:
    """行感知抽取：标签行之后 N 行内找第一个像值的行，值做空白规范化。
    所有结果都附带原文行，便于核对。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    fields, missing = {}, []
    for name, (label, lookahead) in LABEL_RULES.items():
        idx = next((i for i, l in enumerate(lines) if label in l), None)
        if idx is None:
            missing.append(f"{name}(无标签:{label})")
            continue
        # 标签行本身带值时直接用
        inline = lines[idx].replace(label, "", 1).strip(" :：")
        if inline and re.search(VALUE_RE, inline):
            fields[name] = {"value": _norm(inline), "evidence": lines[idx]}
            continue
        got = None
        for k in range(1, lookahead + 1):
            if idx + k >= len(lines):
                break
            cand = lines[idx + k]
            if re.search(VALUE_RE, cand) or cand in {"低", "高", "中"}:
                got = cand
                break
        if got:
            fields[name] = {"value": _norm(got), "evidence": f"{lines[idx]} | {got}"}
        else:
            missing.append(f"{name}(标签有但无值)")

    # 补充：整段文本里的百分比/金额型字段（正则兜底）
    flat = _norm(text)
    for name, pat in {
        "commission_pct": r"蝉选高佣\s*[0-9.]+\s*%",
        "ad_commission_pct": r"投广佣金\s*[0-9.]+\s*%",
        "public_commission_pct": r"公开佣金\s*[0-9.]+\s*%",
    }.items():
        m = re.search(pat, flat)
        if m:
            fields[name] = {"value": _norm(m.group(0)), "evidence": m.group(0)}
    return fields, missing

def capture(page, url: str, raw_dir: Path, item_dir: Path) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(2.5)

    html = page.content()
    try:
        full_text = page.inner_text("body")
    except Exception:  # noqa: BLE001
        full_text = ""
    # 详情容器：页面里同时含「到手价/总库存/佣金」的最小 div，避开导航菜单干扰
    try:
        detail_text = page.evaluate(DETAIL_JS) or ""
    except Exception:  # noqa: BLE001
        detail_text = ""
    text = detail_text or full_text
    pid = slug_from_url(url)

    raw_file = raw_dir / f"{pid}.html"
    txt_file = raw_dir / f"{pid}.txt"
    detail_file = raw_dir / f"{pid}.detail.txt"
    # 关键：必须按字节写入。write_text 在 Windows 会把 \n 翻译成 \r\n，
    # 导致「文件实际字节」与「计算哈希用的字符串」不一致，证据链就断了。
    raw_bytes_ = html.encode("utf-8")
    raw_file.write_bytes(raw_bytes_)
    txt_file.write_bytes(full_text.encode("utf-8"))
    detail_file.write_bytes(detail_text.encode("utf-8"))
    sha = hashlib.sha256(raw_bytes_).hexdigest()

    links = page.evaluate(LINKS_JS)
    controls = page.evaluate(CLICKABLE_JS)
    fields, missing = extract_fields(text)

    item = {
        "page_id": pid,
        "source_url": url,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_html": str(raw_file),
        "raw_text": str(txt_file),
        "raw_sha256": sha,
        "raw_bytes": len(html.encode("utf-8")),
        "title": (page.title() or "").strip(),
        "fields": fields,
        "missing": missing,
        "links": links,
        "controls": controls,
        "counts": {"links": len(links), "controls": len(controls), "fields_found": len(fields)},
    }
    (item_dir / f"{pid}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ids", nargs="*", help="指定 product_id")
    ap.add_argument("--ids-file", default=None, help="从 product_ids.json 读取")
    ap.add_argument("--resume-from", default=None, help="跳过该目录里已采集的 page_id")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    rows = [json.loads(x) for x in (DATA / "library_all.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if args.ids_file:
        ids_map = json.loads(Path(args.ids_file).read_text(encoding="utf-8")).get("ids", {})
        known = {r["product_id"]: r for r in rows}
        rows = [known.get(pid) or {"product_id": pid,
                                   "product_url": f"https://www.chanxuan.com/goods/{pid}.html",
                                   "title": ""} for pid in ids_map]
    if args.ids:
        want = set(args.ids)
        rows = [r for r in rows if r["product_id"] in want]
    if args.resume_from:
        done = {f.stem for f in Path(args.resume_from, "items").glob("*.json")}
        before = len(rows)
        rows = [r for r in rows if r["product_id"] not in done]
        print(f"[resume] 已有 {len(done)} 个，待采 {len(rows)}（总 {before}）", flush=True)
    if not args.ids_file and not args.ids and not args.all and not args.resume_from:
        rows = rows[: args.limit]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / stamp
    raw_dir = out / "raw"
    item_dir = out / "items"
    raw_dir.mkdir(parents=True, exist_ok=True)
    item_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    ok = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        for i, r in enumerate(rows, 1):
            url = r["product_url"]
            try:
                item = capture(page, url, raw_dir, item_dir)
                ok += 1
                print(f"  [{i}/{len(rows)}] OK  {item['page_id'][:16]} "
                      f"fields={item['counts']['fields_found']} links={item['counts']['links']} "
                      f"controls={item['counts']['controls']} {item['raw_bytes']//1024}KB", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(rows)}] FAIL {r['product_id'][:16]}: {type(exc).__name__}: {str(exc)[:90]}", flush=True)
            time.sleep(args.delay)
        page.close()

    manifest = out / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for f in sorted(item_dir.glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            fh.write(json.dumps({k: d[k] for k in ("page_id", "source_url", "captured_at",
                                                   "raw_html", "raw_sha256", "counts")},
                                ensure_ascii=False) + "\n")
    print(f"\n[done] {ok}/{len(rows)} 页，耗时 {time.time()-t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
