"""用严格规则从已保存的原文重新解析，逐字段校验，剔除伪值。

不改原始证据（raw/*.html 不动），只重写 items/*.json 里的 fields，
并为每个字段记录 valid / reason。

  python chanxuan_revalidate.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DIRS = [
    Path(r"D:\start\ds-harness\work\_capture\chanxuan\20261029-154533"),
    Path(r"D:\start\ds-harness\work\_capture\chanxuan-camoufox\20261029-181022"),
]

# 每个字段：(标签, 严格校验)
# value 必须整体匹配对应正则，否则判为无效
RANGE = r"[0-9.]+[万千wWkK]?([-~][0-9.]+[万千wWkK]?)?\+?"
STRICT = {
    "total_sales":      (r"总销量", rf"^{RANGE}$"),
    "stock":            (r"总库存", r"^[0-9.]+[万千wW]?$"),
    "price":            (r"到手价", r"^[0-9]+(\.[0-9]+)?$"),
    "daily_price":      (r"日常价", r"^[¥￥]\s*[0-9]+(\.[0-9]+)?$"),
    "commission":       (r"蝉选高佣", r"^[0-9.]+$"),
    "ad_commission":    (r"投广佣金", r"^[0-9.]+\s*%?$"),
    "public_commission": (r"公开佣金", r"^[0-9.]+\s*%?$"),
    "earn_per_order":   (r"预估每单赚", r"^[0-9]+(\.[0-9]+)?$"),
    "activity_expiry":  (r"活动到期时间", r"^[0-9]{4}-[0-9]{2}-[0-9]{2}.*$"),
    "category_path":    (r"类目", r"^[\u4e00-\u9fa5]+(-[\u4e00-\u9fa5A-Za-z0-9]+)+$"),
    "merchant_score":   (r"商家体验分", r"^[0-9.]{1,4}$"),
}
PERCENT_FIELDS = {"commission", "ad_commission", "public_commission"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_strict(text: str) -> tuple[dict, list[str]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    fields, rejected = {}, []
    for name, (label, pattern) in STRICT.items():
        idx = next((i for i, l in enumerate(lines) if label in l), None)
        if idx is None:
            rejected.append(f"{name}:无标签")
            continue
        inline = norm(lines[idx].replace(label, "", 1).strip(" :："))
        cands = [inline] if inline else []
        cands += [norm(lines[idx + k]) for k in range(1, 4) if idx + k < len(lines)]
        hit = next((c for c in cands if c and re.fullmatch(pattern, c)), None)
        if hit is None:
            rejected.append(f"{name}:值不合法")
            continue
        # 百分比字段做范围检查
        if name in PERCENT_FIELDS:
            try:
                v = float(re.sub(r"[^0-9.]", "", hit) or 0)
            except ValueError:
                v = -1
            if not (0 <= v <= 100):
                rejected.append(f"{name}:超出 0-100")
                continue
        fields[name] = {"value": hit, "evidence": f"{lines[idx]} | {hit}", "valid": True}
    return fields, rejected


def main() -> int:
    total = valid_pages = 0
    stat = {"ok": 0, "insufficient": 0}
    for d in DIRS:
        for f in sorted(d.glob("items/*.json")):
            obj = json.loads(f.read_text(encoding="utf-8"))
            raw_detail = Path(obj.get("raw_detail_text") or obj["raw_text"])
            if not raw_detail.exists():
                continue
            text = raw_detail.read_text(encoding="utf-8", errors="replace")
            fields, rejected = parse_strict(text)
            obj["fields"] = fields
            obj["rejected_fields"] = rejected
            n = len(fields)
            obj["data_status"] = "ok" if n >= 4 else "insufficient"
            obj["counts"]["fields_found"] = n
            stat[obj["data_status"]] = stat.get(obj["data_status"], 0) + 1
            total += 1
            if obj["data_status"] == "ok":
                valid_pages += 1
            f.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"处理 {total} 页：有效 {valid_pages}，不足 {total - valid_pages}")
    print("状态分布:", stat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
