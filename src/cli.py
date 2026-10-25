"""mcd-store-truth 命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_client import McpError, McdMcpClient  # noqa: E402
from store_engine import (Coupon, Store, render_matrix, summarize,  # noqa: E402
                          verify_coupon, verify_matrix)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_STORES = os.path.join(BASE, "samples", "stores_sample.json")
SAMPLE_COUPONS = os.path.join(BASE, "samples", "coupons_sample.json")

SCENARIOS = {"pickup": 1, "drivethrough": 5}


def _load(path, key="list"):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get(key) or data.get("data") or data


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #
def get_stores(args):
    if not args.offline:
        try:
            client = McdMcpClient.from_env()
            be_type = SCENARIOS.get(args.scenario, 1)
            raw = client.call_tool("query-nearby-stores", {
                "beType": be_type, "searchType": 2,
                "city": args.city, "keyword": args.keyword})
            if isinstance(raw, dict) and isinstance(raw.get("data"), list):
                raw = raw["data"]           # 信封拆壳（client 已拆，这里是兜底）
            items = raw if isinstance(raw, list) else []
            # 少数接口可能返回伪表格/文本，直接提示而不假装拿到数据
            if isinstance(raw, (str, dict)) and not isinstance(raw, list):
                print("[warn] 门店数据不是数组形态：%s" % type(raw).__name__, file=sys.stderr)
            stores = [Store.from_raw(s) for s in items if isinstance(s, dict)]
            if stores:
                return stores, "麦当劳 MCP query-nearby-stores 实时返回"
            print("[warn] 门店返回为空，回落样例", file=sys.stderr)
        except McpError as exc:
            print("[warn] MCP 调用失败，回落样例：%s" % exc, file=sys.stderr)
    items = _load(SAMPLE_STORES)
    return [Store.from_raw(s) for s in items], "本地样例 %s（结构同真实返回，数值为示例）" % os.path.basename(SAMPLE_STORES)


def _as_list(raw):
    """把工具返回收敛成 list：支持 [..] / {"coupons":[]} / {"list":[]} / {"data":[]}。"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if isinstance(raw.get("data"), list):
            return raw["data"]
        for key in ("coupons", "list", "items"):
            if isinstance(raw.get(key), list):
                return raw[key]
    return []


def get_coupons(args, store_code=""):
    if not args.offline:
        try:
            client = McdMcpClient.from_env()
            items = _as_list(client.call_tool("query-my-coupons"))
            # 门店券是另一条来源，官方四条来源设计里它最容易漏
            if store_code:
                items = items + _as_list(client.call_tool(
                    "query-store-coupons", {"storeCode": store_code}))
            coupons = [Coupon.from_raw(c) for c in items if isinstance(c, dict)]
            if coupons:
                return coupons, "麦当劳 MCP query-my-coupons / query-store-coupons 实时返回"
            print("[warn] 券返回为空，回落样例", file=sys.stderr)
        except McpError as exc:
            print("[warn] MCP 调用失败，回落样例：%s" % exc, file=sys.stderr)
    items = _load(SAMPLE_COUPONS)
    return [Coupon.from_raw(c) for c in items], "本地样例 %s（数值为示例）" % os.path.basename(SAMPLE_COUPONS)


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def cmd_selfcheck(_a):
    st = McdMcpClient.token_status()
    print("=== 环境自检 ===")
    print("MCD_MCP_TOKEN: %s" % ("已配置" if st["configured"] else "未配置"))
    print("tokenLength  : %d" % st["tokenLength"])
    print("结论：%s" % ("Token 已就绪" if st["configured"] else "未配置 Token，可用 --offline 体验"))
    return 0


def cmd_stores(args):
    stores, note = get_stores(args)
    if args.json:
        print(json.dumps([s.__dict__ for s in stores], ensure_ascii=False, indent=2))
    else:
        print("【可用门店】%d 家" % len(stores))
        for s in stores:
            print("  · %s｜%s%s" % (s.label, s.address or "地址未提供",
                                   "｜%s" % s.distance if s.distance else ""))
        print("数据来源：%s" % note)
    return 0


def cmd_coupons(args):
    coupons, note = get_coupons(args)
    if args.json:
        print(json.dumps([c.__dict__ for c in coupons], ensure_ascii=False, indent=2))
    else:
        print("【我的券】%d 张" % len(coupons))
        for c in coupons:
            print("  · %s｜渠道=%s｜有效期=%s ~ %s｜%s" % (
                c.label, c.channel, c.start_time or "未提供",
                c.end_time or "未提供", c.status or "状态未提供"))
        print("数据来源：%s" % note)
    return 0


def cmd_verify(args):
    stores, _ = get_stores(args)
    coupons, coupon_note = get_coupons(args)
    store = next((s for s in stores if s.store_code == args.store), None)
    if not store:
        print("未找到门店 %s，可用：%s" % (args.store, "、".join(s.store_code for s in stores)))
        return 2
    needle = args.coupon.strip().lower()
    coupon = next((c for c in coupons
                   if needle in str(c.name).lower() or needle in str(c.coupon_id).lower()), None)
    if not coupon:
        print("未找到匹配的券：%s" % args.coupon)
        return 2
    res = verify_coupon(coupon, store, scenario=args.scenario)
    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
        return 0
    print("【确定性校验】%s @ %s" % (coupon.label, store.label))
    print("场景：%s　结论：%s" % (args.scenario, res.as_dict()["levelLabel"]))
    for e in res.evidence:
        print("  ✅ %s" % e)
    for b in res.blockers:
        print("  ❌ %s" % b)
    for m in res.missing:
        print("  ❓ 缺失字段：%s" % m)
    print("数据来源：%s" % coupon_note)
    return 0


def cmd_matrix(args):
    stores, _ = get_stores(args)
    coupons, note = get_coupons(args)
    results = verify_matrix(coupons, stores, scenario=args.scenario, today=args.today or "")
    summary = summarize(results)
    if args.json:
        print(json.dumps({
            "summary": summary,
            "rows": [r.as_dict() for r in results],
        }, ensure_ascii=False, indent=2))
        return 0
    print("【券 × 门店 真值矩阵】%d 张券 × %d 家门店" % (len(coupons), len(stores)))
    print(render_matrix(coupons, stores, results))
    print("统计：✅ 可以用 %d　❌ 用不了 %d　❓ 判不了 %d" % (
        summary["counts"]["TRUE"], summary["counts"]["FALSE"], summary["counts"]["UNKNOWN"]))
    print("数据来源：%s" % note)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="mcd-store-truth",
        description="麦门店到_CFG确定性校验器：这张券，在这家店，此刻到底能不能用")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("selfcheck", help="Token 自检").set_defaults(func=cmd_selfcheck)

    sp = sub.add_parser("stores", help="查附近门店")
    sp.add_argument("--city", default="成都")
    sp.add_argument("--keyword", default="春熙路")
    sp.add_argument("--scenario", default="pickup", choices=list(SCENARIOS))
    sp.add_argument("--offline", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stores)

    sp = sub.add_parser("coupons", help="查我持有的券")
    sp.add_argument("--offline", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_coupons)

    sp = sub.add_parser("verify", help="单张券 × 单个门店的确定性校验")
    sp.add_argument("--coupon", required=True, help="券名称或券 ID 片段")
    sp.add_argument("--store", required=True, help="门店编码")
    sp.add_argument("--scenario", default="pickup", choices=["pickup", "delivery", "drivethrough"])
    sp.add_argument("--city", default="成都")
    sp.add_argument("--keyword", default="春熙路")
    sp.add_argument("--offline", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("matrix", help="券 × 门店 全量真值矩阵")
    sp.add_argument("--scenario", default="pickup", choices=["pickup", "delivery", "drivethrough"])
    sp.add_argument("--city", default="成都")
    sp.add_argument("--keyword", default="春熙路")
    sp.add_argument("--today", default="", help="覆盖今天的日期，便于回归测试")
    sp.add_argument("--offline", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_matrix)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
