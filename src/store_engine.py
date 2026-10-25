"""确定性校验引擎。

这个项目的全部价值就一句话：**不许猜**。

麦当劳的券体系很碎：有到店券、外送券、门店专属券、品类券、时段券…… 
字段还会缺失。绝大多数"能不能用"的失败感，来自用户默认以为能用、柜台说不能。

所以本项目把每个结论强制归入三态：

  * TRUE   「可以用」 —— 所有可得字段都通过，且没有未知项阻塞
  * FALSE  「用不了」 —— 至少有一条**硬证据**明确否决
  * UNKNOWN「判不了」 —— 关键字段缺失，宁可认怂，也不给你一个可能错的答案

每个结论都必须带 `evidence` 列表，写明依据的字段和值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", "UNKNOWN"
_SYMBOL = {TRUE: "✅ 可以用", FALSE: "❌ 用不了", UNKNOWN: "❓ 判不了"}

# 渠道口径：券的适用场景 -> 支持的 beType
CHANNEL_MAP = {
    "pickup": ["自取", "到店", "堂食", "外带", "pickup"],
    "delivery": ["外送", "外卖", "麦乐送", "delivery"],
    "drivethrough": ["得来速", "车道", "dt", "drive"],
    "any": ["通用", "全渠道", "any", "all"],
}


def _text(record: Dict[str, Any], *keys) -> Optional[str]:
    for k in keys:
        v = record.get(k)
        if v not in (None, "", []):
            return str(v).strip()
    return None


def _first_list(record: Dict[str, Any], *keys) -> Optional[List[str]]:
    """提取列表字段。**区分「字段缺失(None)」与「显式空列表([])」**——
    这在业务上完全不同：前者是判不了(UNKNOWN)，后者是全门店可用。"""
    for k in keys:
        if k not in record:
            continue
        v = record.get(k)
        if isinstance(v, list):
            return [str(x) for x in v]      # 空列表原样返回 []
        if isinstance(v, str):
            return [s.strip() for s in v.replace(";", ",").split(",") if s.strip()]
    return None


@dataclass
class Store:
    store_code: str
    name: str = ""
    be_code: str = ""
    address: str = ""
    distance: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "Store":
        raw = raw or {}
        return cls(
            store_code=str(_text(raw, "storeCode", "store_code", "门店编码", "code") or ""),
            name=str(_text(raw, "storeName", "name", "门店名称", "title") or "未知门店"),
            be_code=str(_text(raw, "beCode", "be_code") or ""),
            address=str(_text(raw, "address", "门店地址") or ""),
            distance=_text(raw, "distance", "距离"),
            raw=raw,
        )

    @property
    def label(self) -> str:
        return "%s(%s)" % (self.name, self.store_code) if self.store_code else self.name


@dataclass
class Coupon:
    coupon_id: str
    name: str = ""
    channel: str = "unknown"        # pickup / delivery / drivethrough / any / unknown
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    store_codes: Optional[List[str]] = None   # None 表示未返回该字段（区别于空列表）
    product_codes: Optional[List[str]] = None
    status: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "Coupon":
        raw = raw or {}
        channel_raw = (_text(raw, "channel", "渠道", "scene", "beTypeText") or "").lower()
        channel = "unknown"
        if channel_raw:  # 空字符串不得参与匹配，否则 "" in alias 恒真
            for canonical, aliases in CHANNEL_MAP.items():
                if any(channel_raw == a or (len(channel_raw) > 1 and (a in channel_raw or channel_raw in a))
                       for a in aliases):
                    channel = canonical
                    break
        return cls(
            coupon_id=str(_text(raw, "couponId", "id", "券ID", "couponNo") or ""),
            name=str(_text(raw, "couponName", "name", "券名称", "title") or "未命名优惠券"),
            channel=channel,
            start_time=_text(raw, "startTime", "validStartTime", "生效时间", "beginTime"),
            end_time=_text(raw, "endTime", "validEndTime", "失效时间", "expireTime"),
            store_codes=_first_list(raw, "storeCodes", "applicableStores", "适用门店"),
            product_codes=_first_list(raw, "productCodes", "products", "适用商品"),
            status=str(_text(raw, "status", "statusText", "状态") or ""),
            raw=raw,
        )

    @property
    def label(self) -> str:
        return self.name or self.coupon_id


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    coupon: Coupon
    store: Optional[Store]
    level: str
    evidence: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coupon": self.coupon.label,
            "store": self.store.label if self.store else None,
            "level": self.level,
            "levelLabel": _SYMBOL.get(self.level, self.level),
            "evidence": self.evidence,
            "blockers": self.blockers,
            "missing": self.missing,
        }


def today_str() -> str:
    import datetime
    return datetime.date.today().isoformat()


def verify_coupon(coupon: Coupon, store: Optional[Store],
                  scenario: str = "pickup", today: str = "") -> CheckResult:
    """校验一张券在某个门店（某场景）能否使用。"""
    today = today or today_str()
    res = CheckResult(coupon=coupon, store=store, level=TRUE, evidence=[])

    # 1) 有效期 —— 只看有值的那边，缺失就记为缺失而不是否决
    if coupon.start_time:
        if str(coupon.start_time) > today:
            res.blockers.append("尚未生效：生效日 %s > 今日 %s" % (coupon.start_time, today))
        else:
            res.evidence.append("活动期内：生效日 %s ≤ 今日 %s" % (coupon.start_time, today))
    else:
        res.missing.append("生效时间")
    if coupon.end_time:
        if str(coupon.end_time) < today:
            res.blockers.append("已过期：失效日 %s < 今日 %s" % (coupon.end_time, today))
        else:
            res.evidence.append("未过期：失效日 %s ≥ 今日 %s" % (coupon.end_time, today))
    else:
        res.missing.append("失效时间")

    # 2) 渠道匹配
    if coupon.channel == "unknown":
        res.missing.append("适用渠道")
    elif coupon.channel == "any":
        res.evidence.append("渠道：券标注为全渠道通用")
    elif coupon.channel == scenario:
        res.evidence.append("渠道匹配：券=%s，本次场景=%s" % (coupon.channel, scenario))
    else:
        res.blockers.append("渠道不匹配：券=%s，本次场景=%s" % (coupon.channel, scenario))

    # 3) 适用门店白名单
    if store is None:
        res.missing.append("门店信息")
    elif coupon.store_codes is None:
        res.missing.append("适用门店清单（服务端未返回，无法排除限门店券的可能）")
    elif not coupon.store_codes:
        res.evidence.append("适用门店清单为空，按全门店可用理解")
    elif store.store_code and store.store_code in coupon.store_codes:
        res.evidence.append("门店在适用清单内：%s" % store.store_code)
    else:
        res.blockers.append("门店不在适用清单内：门店=%s，清单=%s 项" % (
            store.store_code or "未知", len(coupon.store_codes)))

    # 4) 状态
    if coupon.status and any(k in coupon.status for k in ("已使用", "已失效", "过期", "冻结")):
        res.blockers.append("券状态异常：%s" % coupon.status)

    # 5) 汇总
    if res.blockers:
        res.level = FALSE
    elif res.missing:
        res.level = UNKNOWN
    else:
        res.evidence.append("可得字段全部通过；最终可否核销以官方渠道实时结果为准")
    return res


def verify_matrix(coupons: List[Coupon], stores: List[Store],
                  scenario: str = "pickup", today: str = "") -> List[CheckResult]:
    return [verify_coupon(c, s, scenario=scenario, today=today)
            for c in coupons for s in stores]


def summarize(results: List[CheckResult]) -> Dict[str, Any]:
    counts = {TRUE: 0, FALSE: 0, UNKNOWN: 0}
    for r in results:
        counts[r.level] += 1
    top_unknown = [r for r in results if r.level == UNKNOWN][:3]
    suggestions = []
    for r in top_unknown:
        suggestions.append("「%s」判不了，缺：%s" % (r.coupon.label, "、".join(r.missing) or "未知原因"))
    return {"total": len(results), "counts": counts, "suggestions": suggestions}


def render_matrix(coupons: List[Coupon], stores: List[Store], results: List[CheckResult]) -> str:
    """券 × 门店 真值矩阵。"""
    index = {(r.coupon.coupon_id, r.store.store_code if r.store else ""): r
             for r in results}
    mark = {TRUE: " ✅ ", FALSE: " ❌ ", UNKNOWN: " ❓ "}
    lines = []
    header = "券 \\ 门店".ljust(24) + "".join(
        ("%s" % (s.store_code or s.name)[:10]).ljust(12) for s in stores)
    lines.append(header)
    for c in coupons:
        row = (c.label[:22]).ljust(24)
        for s in stores:
            r = index.get((c.coupon_id, s.store_code))
            row += (mark.get(r.level, " ? ") if r else " ? ").ljust(12)
        lines.append(row)
    lines.append("")
    lines.append("图例：✅ 可以用（可得字段均通过）　❌ 用不了（有硬证据否决）　❓ 判不了（缺关键字段）")
    return "\n".join(lines)
