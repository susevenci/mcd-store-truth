# MCP_INTEGRATION.md

## 1. 接入方式

| 项目 | 值 |
|---|---|
| MCP Server | `mcd-mcp`（麦当劳中国官方 MCP Server） |
| 传输 | Streamable HTTP，JSON-RPC 2.0（兼容 SSE） |
| Endpoint | `https://mcp.mcd.cn` |
| 鉴权 | `Authorization: Bearer ${MCD_MCP_TOKEN}` |
| 客户端 | `src/mcp_client.py`（纯标准库，节流 300 次/分钟 + 429 退避） |

## 2. Tool 清单

| Tool | 命令 | 关键字段 |
|---|---|---|
| `query-nearby-stores` | `stores` / `matrix` / `verify` | `storeCode`、`storeName`、`beCode`（beType=5 时）、`address`、`distance` |
| `query-my-coupons` | `coupons` / `matrix` | `couponId`、`couponName`、`channel`、`startTime`、`endTime`、`storeCodes`、`statusText` |
| `query-store-coupons` | `coupons`（合并来源） | 同上，**门店专属券只在这条来源出现** |
| `calculate-price` | 核价确认（可选扩展） | 最终可否核销以它为准 |
| `query-meal-assistance` | 团餐场景扩展点 | 助餐服务可用性 |

> 本项目**全部为只读调用**，不下单、不消耗积分、不写入账号数据。

## 3. 调用流程

```
①  tools/call query-nearby-stores   {beType:1|5, searchType:2, city, keyword}
        │                            └ beType=5(得来速) 时后续必须带 beCode
        ▼
②  tools/call query-my-coupons      └ 券的四路来源之一，最全的一条
    tools/call query-store-coupons  {storeCode}   ← 专属券在这里，极易漏
        │
        ▼
③  Coupon.from_raw / Store.from_raw
        │  关键细节：区分「字段缺失 None」与「显式空列表 []」
        │           前者 → UNKNOWN，后者 → 视为全门店可用
        ▼
④  verify_coupon(coupon, store, scenario)
        ├─ 有效期      start/end 与今日比对，单边缺失记为缺失
        ├─ 渠道        pickup / delivery / drivethrough / any
        ├─ 门店清单    白名单比对；字段缺失 → UNKNOWN
        └─ 券状态      已使用 / 已失效 / 冻结 → 硬否决
        ▼
⑤  summarize + render_matrix
```

## 4. 为什么要把券的两个来源合并

官方的四路来源设计里，券分散在多条接口（卡包券、门店券、会员权益、活动券）。
只查 `query-my-coupons` 会漏掉门店专属券——而门店专属券恰恰是本工具最核心的输入。
因此 `coupons` 命令默认做**双来源合并**，这也是很多点餐类 Skill 容易忽略的一步。

## 5. UNKNOWN 设计的业务价值

把"不知道"做成一等公民，是这个项目和普通推荐工具最大的区别：

* 对用户：避免在柜台前被拒的尴尬；
* 对 Agent：避免下游拿着错误结论去 `create-order` 失败；
* 对开发者：报告里明确写出"缺哪个字段"，比一个含糊的"可能可以"有用得多。

## 6. 配置示例

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

仓库内不含任何真实 Token。

## 附：接口实测留痕

本项目在 2026-10-25 对麦当劳 MCP 做过真实调用，完整记录（含当时账号状态、原始输出、复现命令）
见 docs/2026-10-25-实测记录.md。

实测确认的两个关键形态（代码已适配）：
1. 响应统一为 {"success":..,"code":..,"data":<业务数据>} 信封，业务数据在 data 内；
2. structuredContent 与 content[0].text 并存，客户端优先取 structuredContent。
