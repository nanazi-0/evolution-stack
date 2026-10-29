"""直播切片选品打分（规则模型，不用 LLM）。

目标不是「佣金最高」，而是「持续赚钱」——长期稳定的佣金现金流。
所以模型拆成五个维度，每个都有可解释的来源：

  1. 单均佣金   price × commission_rate       每成交一单到手多少
  2. 出单规模   30日销量中值                   卖不卖得动（log 压缩）
  3. 稳定性     7日日均 ÷ 30日日均             在起量 / 稳定 / 在衰减
  4. 口碑风险   商家综合体验分                 低分通常意味着退货高
  5. 复购潜力   类目系数                       消耗品才会重复买

竞争度（在带达人数）作为**负向**调节：人越多，单达人分到的流量越少。

  python clip_score.py --top 20
  python clip_score.py --top 50 --csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

DATA = Path(r"D:\start\ds-harness\evolution-stack\local\agent-evolution-platform\targets\clip\data")
SRC = DATA / "library_all.jsonl"

# 类目复购系数：消耗完就得再买 = 高；耐用品 = 低
CATEGORY_REPEAT = {
    "食品饮料": 1.0, "日用百货": 1.0, "生鲜蔬果": 0.95, "母婴用品": 0.9,
    "美妆护肤": 0.85, "医药保健": 0.8, "宠物用品": 0.85, "鲜花绿植": 0.6,
    "服饰内衣": 0.5, "鞋靴箱包": 0.45, "运动户外": 0.45, "玩具乐器": 0.4,
    "3C数码": 0.3, "厨卫家电": 0.25, "家居家纺": 0.2, "家具建材": 0.15,
    "珠宝饰品": 0.2, "钟表配饰": 0.2, "图书教育": 0.3, "礼品文创": 0.35,
    "汽配摩托": 0.35, "奢侈品": 0.2, "原料包装": 0.5, "本地生活": 0.5,
    "二手商品": 0.3, "其他": 0.5,
}

PRESETS = {
    # 持续赚钱：复购与销量优先，单均佣金让位
    "steady": {"commission": 0.20, "volume": 0.30, "stability": 0.15,
               "quality": 0.10, "repeat": 0.25},
    # 短期现金流：单均佣金优先
    "profit": {"commission": 0.40, "volume": 0.30, "stability": 0.15,
               "quality": 0.10, "repeat": 0.05},
    # 均衡（默认）
    "balanced": {"commission": 0.30, "volume": 0.30, "stability": 0.20,
                 "quality": 0.10, "repeat": 0.10},
}

WEIGHTS = {           # 可按业务偏好调
    "commission": 0.30,
    "volume": 0.30,
    "stability": 0.20,
    "quality": 0.10,
    "repeat": 0.10,
}
CREATOR_PENALTY = 0.25   # 竞争惩罚强度
HARD_FILTERS = {"min_commission": 0.08, "min_score": 4.5, "min_sales_30d": 3000}


def mid(v):
    if v is None:
        return None
    if isinstance(v, list) and len(v) == 2:
        return sum(v) / 2
    if isinstance(v, (int, float)):
        return float(v)
    return None


def norm_log(x: float, lo: float, hi: float) -> float:
    """对数归一化到 0..1"""
    if x is None or x <= 0:
        return 0.0
    x = max(lo, min(hi, x))
    return (math.log10(x) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))


def score_one(p: dict) -> dict | None:
    price = p.get("price")
    rate = p.get("commission_rate")
    s30 = mid(p.get("sales_30d"))
    s7 = mid(p.get("sales_7d"))
    score_m = p.get("merchant_score")
    cat = p.get("category") or "其他"
    creators = mid(p.get("creators_count"))

    if not price or not rate or s30 is None:
        return None
    # 硬过滤
    if rate < HARD_FILTERS["min_commission"]:
        return None
    if score_m is not None and score_m < HARD_FILTERS["min_score"]:
        return None
    if s30 < HARD_FILTERS["min_sales_30d"]:
        return None

    earn = price * rate
    after_days = s30 / 30.0
    recent = (s7 / 7.0) if s7 else None

    # 稳定性：近 7 日日均 / 30 日日均
    # 注意：销量是**区间档位**，7 日与 30 日落在同一档时，比值恒等于 30/7≈4.29，
    # 那是档位粗糙度造成的假信号，不是真在暴涨。必须识别并降级为"同档无法判断"。
    same_bucket = (p.get("sales_7d") == p.get("sales_30d"))
    if same_bucket or not recent or after_days <= 0:
        ratio, stability = None, 0.55        # 中性：不做趋势加成，也不惩罚
        trend_note = "同档无法判断"
    else:
        ratio = recent / after_days
        if ratio >= 1.0:
            stability = min(1.0, 0.6 + min(ratio - 1.0, 1.0) * 0.4)
            trend_note = "起量"
        else:
            stability = max(0.0, ratio)
            trend_note = "衰减"

    s_comm = norm_log(earn, 0.5, 30)          # 单均佣金 0.5~30 元
    s_vol = norm_log(s30, 1000, 300000)      # 30 日销量 1k~30万
    s_qual = ((score_m if score_m is not None else 4.6) - 4.0) / 1.0   # 4.0~5.0 -> 0.4~1.0
    s_qual = max(0.0, min(1.2, s_qual))
    s_repeat = CATEGORY_REPEAT.get(cat, 0.5)

    base = (WEIGHTS["commission"] * s_comm + WEIGHTS["volume"] * s_vol +
            WEIGHTS["stability"] * stability + WEIGHTS["quality"] * s_qual +
            WEIGHTS["repeat"] * s_repeat)

    # 竞争：在带人数越多，单达人分到的越少（对数，封顶）
    comp = 0.0
    if creators:
        comp = CREATOR_PENALTY * norm_log(creators, 50, 5000)
    total = max(0.0, base - comp) * 100

    return {
        "product_id": p["product_id"],
        "title": p["title"],
        "category": cat,
        "price": price,
        "commission_rate": rate,
        "earn_per_order": round(earn, 2),
        "sales_30d_mid": round(s30),
        "sales_7d_mid": round(s7) if s7 else None,
        "trend_ratio": round(ratio, 2) if ratio else None,
        "trend_note": trend_note,
        "merchant_score": score_m,
        "creators": round(creators) if creators else None,
        "channel": p.get("channel"),
        "shop": p.get("shop"),
        "score": round(total, 1),
        "parts": {
            "佣金": round(s_comm * 100, 0),
            "销量": round(s_vol * 100, 0),
            "稳定": round(stability * 100, 0),
            "口碑": round(s_qual * 100, 0),
            "复购": round(s_repeat * 100, 0),
            "竞争扣分": round(comp * 100, 0),
        },
        "url": p.get("product_url"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--preset", default="steady", choices=list(PRESETS), help="打分预设")
    args = ap.parse_args()

    global WEIGHTS
    WEIGHTS = PRESETS[args.preset]
    print(f"[info] 预设 {args.preset}: " + ", ".join(f"{k}={v:.0%}" for k, v in WEIGHTS.items()))
    rows = [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]
    scored = [s for s in (score_one(r) for r in rows) if s]
    scored.sort(key=lambda x: x["score"], reverse=True)
    passed = len(scored)
    print(f"[info] 池子 {len(rows)} 条 -> 通过硬过滤 {passed} 条（佣金≥{HARD_FILTERS['min_commission']:.0%}、"
          f"体验分≥{HARD_FILTERS['min_score']}、30日销量≥{HARD_FILTERS['min_sales_30d']}）\n")

    print(f"{'#':>3} {'分':>5} {'单价':>7} {'佣金':>5} {'每单赚':>7} {'30日销量':>9} {'趋势':>5} {'分':>4} {'在带':>6}  商品")
    for i, s in enumerate(scored[: args.top], 1):
        print(f"{i:>3} {s['score']:>5.1f} {s['price']:>7.1f} {s['commission_rate']*100:>4.0f}% "
              f"{s['earn_per_order']:>7.2f} {s['sales_30d_mid']:>9,} "
              f"{(s['trend_ratio'] if s['trend_ratio'] is not None else 0):>5.2f} "
              f"{(s['merchant_score'] or 0):>4.1f} {s['creators'] or 0:>6}  {s['title'][:26]}")

    if args.csv:
        dst = DATA / "clip_top.csv"
        with dst.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["排名", "分数", "商品", "类目", "单价", "佣金率", "每单赚", "30日销量",
                        "7日销量", "趋势比", "体验分", "在带人数", "带货方式", "店铺", "链接"])
            for i, s in enumerate(scored, 1):
                w.writerow([i, s["score"], s["title"], s["category"], s["price"],
                            f"{s['commission_rate']*100:.0f}%", s["earn_per_order"],
                            s["sales_30d_mid"], s["sales_7d_mid"], s["trend_ratio"],
                            s["merchant_score"], s["creators"], s["channel"], s["shop"], s["url"]])
        print(f"\n[csv] {len(scored)} 条 -> {dst}")

    (DATA / "clip_scored.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in scored), encoding="utf-8")
    print(f"[jsonl] -> {DATA / 'clip_scored.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
