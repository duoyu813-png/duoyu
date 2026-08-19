"""GitHub Actions 静态看板生成器（无 Flask / 无数据库）

流程：
  1. 东财抓全市场可转债行情 -> 基础信息/概念/净资产 -> merge 富集
  2. 运行全部轮动策略
  3. 集思录抓封闭基金折价（游客 20 条内 -> 东财 LOF 兜底）
  4. 东财抓待发可转债（抢权）
  5. 渲染静态单页看板 index.html + 数据快照 data.json

输出到 dist/ 目录，由 GitHub Actions 部署到 GitHub Pages。
"""
import json
import os
import sys
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.eastmoney import EastMoneyScraper
from scrapers.jisilu import JisiluScraper
from scrapers.data_merger import merge_cb_data
from strategies.cb_strategies import run_all_strategies

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")


def _fmt(v, nd=2):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(getattr(v, "strftime", lambda f: v)(f"%Y-%m-%d")) if hasattr(v, "strftime") else str(v)


def _date_str(v):
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if s and s not in ("-", "None") else ""


_NUM_FIELDS = [
    "price", "change_pct", "premium_rate", "conversion_value", "pure_bond_value",
    "transfer_price", "stock_price", "ytm_before_tax", "remaining_years",
    "remaining_size", "net_assets", "redeem_trig_price", "redeem_price", "volume",
    "turnover_rate",
]


def _to_num(v):
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_date(v):
    if v is None or v in ("-", "None", ""):
        return None
    s = str(v).strip()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, IndexError):
        return None


def _build_bond_dicts(merged: list[dict]) -> list[dict]:
    """把 merge_cb_data 输出的 live dict 还原成策略引擎期望的字段形态（数值转 float、日期转 date）"""
    out = []
    for b in merged:
        d = dict(b)
        for key in _NUM_FIELDS:
            d[key] = _to_num(b.get(key))
        d["list_date"] = _to_date(b.get("list_date_str"))
        d["conversion_period_start"] = _to_date(b.get("conversion_period_start_str"))
        d["maturity_date"] = _to_date(b.get("maturity_date_str"))
        # SQLAlchemy 布尔类字段
        d["announced_redemption"] = bool(b.get("announced_redemption"))
        d["stock_st"] = bool(b.get("stock_st"))
        d["redemption_days"] = int(b.get("redemption_days") or 0)
        out.append(d)
    return out


def _render_table(headers, rows):
    thead = "".join(f"<th>{h}</th>" for h in headers)
    tbody = []
    for r in rows:
        tds = []
        for cell in r:
            cls = ""
            s = str(cell)
            if "%" in s:
                try:
                    v = float(s.rstrip("%"))
                    if v > 0:
                        cls = " class='green'"
                    elif v < 0:
                        cls = " class='red'"
                except ValueError:
                    pass
            tds.append(f"<td{cls}>{s}</td>")
        tbody.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(tbody)}</tbody></table>"


def fetch_funds_merged():
    """封闭基金：两源融合，返回全量列表（无需集思录 cookie）

    集思录（官方封基/定开，字段全：折价年化/剩余年限/到期日/类型），游客仅前 20 条/共 31 条；
    东财 MK0404/0405 板块（全市场场内基金 150+ 条，含现价/净值/折价/上市日，无到期日字段）。
    以东财为全集主体，集思录覆盖富集字段；这样即使游客态也能看到完整版，真正封基字段更准确。
    """
    jisilu = JisiluScraper.fetch_closed_funds()
    em = EastMoneyScraper.fetch_traded_funds()
    base = em if em else jisilu
    ji_map = {}
    for f in jisilu:
        code = f.get("code")
        if code:
            ji_map[code] = f
    out = []
    for f in base:
        code = f.get("code", "")
        j = ji_map.get(code)
        # 名称/净值/价格缺失时用集思录补齐
        merged = {
            "code": code,
            "name": f.get("name") or (j or {}).get("name", ""),
            "price": f.get("price") if f.get("price") is not None else (j or {}).get("price"),
            "change_pct": f.get("change_pct"),
            "nav": f.get("nav") if f.get("nav") is not None else (j or {}).get("nav"),
            "discount_rate": f.get("discount_rate") if f.get("discount_rate") is not None else (j or {}).get("discount_rate"),
            # 富集字段以集思录为准（官方口径）
            "discount_annual": (j or {}).get("discount_annual"),
            "remaining_years": (j or {}).get("remaining_years"),
            "maturity_date": (j or {}).get("maturity_date") or f.get("expire_date"),
            "last_volume": (j or {}).get("last_volume") if not None else f.get("amount"),
            "total_cap": f.get("total_cap"),
            "fund_type": (j or {}).get("fund_type") or ("定开" if (j or {}).get("notes") else str(f.get("fund_type") or "")),
            "notes": (j or {}).get("notes", ""),
        }
        out.append(merged)
    # 东财为主且集思录也有数据时，把集思录中不在东财里的条目也补进去（保证官方全集完整）
    ji_codes = set(ji_map.keys())
    em_codes = {f.get("code") for f in (em or [])}
    for code in ji_codes - em_codes:
        j = ji_map[code]
        out.append(dict(j))
    # 默认按折价率升序（折价越多越靠前）
    out.sort(key=lambda f: (f.get("discount_rate") is None, f.get("discount_rate") or 0))
    return out


def main() -> int:
    t0 = time.time()
    print("[sitegen] 开始生成静态看板")
    report = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sections": {}}

    # ---------- 1) 可转债 ----------
    live_data = EastMoneyScraper.fetch_cb_list()
    if live_data:
        fundamentals = EastMoneyScraper.fetch_cb_fundamentals()
        stock_codes = [b["stock_code"] for b in live_data if b.get("stock_code")]
        concepts = EastMoneyScraper.fetch_stock_concepts(stock_codes)
        financials = EastMoneyScraper.fetch_stock_financials(stock_codes)
        merged = merge_cb_data(live_data, fundamentals, concepts, financials)
        bonds = _build_bond_dicts(merged)
        strategies = run_all_strategies(bonds)
        report["sections"]["cb_count"] = len(merged)
    else:
        strategies = {}
        report["sections"]["cb_count"] = 0

    # ---------- 2) 封闭基金 ----------
    funds = fetch_funds_merged()
    report["sections"]["fund_count"] = len(funds)

    # ---------- 3) 待发可转债 ----------
    issues = EastMoneyScraper.fetch_new_cb_issues()
    report["sections"]["issue_count"] = len(issues)

    # ---------- 4) 渲染 HTML ----------
    strategy_html = ""
    for key, data in strategies.items():
        rows = []
        for b in data["bonds"][:20]:
            rows.append([
                b.get("code", ""), b.get("name", ""),
                _fmt(b.get("price")),
                _fmt(b.get("premium_rate")) + "%" if b.get("premium_rate") is not None else "-",
                _fmt(b.get("remaining_size")) if b.get("remaining_size") is not None else "-",
                _fmt(b.get("ytm_before_tax")) + "%" if b.get("ytm_before_tax") is not None else "-",
                str(b.get("concept", "") or ""),
            ])
        if not rows:
            continue
        strategy_html += f"""
        <div class="card">
          <h2>{data['name']} <span class="badge">{len(data['bonds'])}</span></h2>
          {_render_table(["代码","名称","价格","溢价%","剩余规模(亿)","税前YTM%","概念"], rows)}
        </div>"""

    fund_html = _render_table(
        ["代码", "名称", "现价", "涨跌%", "净值", "折价%", "折价年化%", "剩余年限", "到期日", "类型"],
        [[
            f.get("code", ""), f.get("name", ""),
            _fmt(f.get("price")),
            _fmt(f.get("change_pct")) + "%" if f.get("change_pct") is not None else "-",
            _fmt(f.get("nav")),
            _fmt(f.get("discount_rate")) + "%" if f.get("discount_rate") is not None else "-",
            _fmt(f.get("discount_annual")) + "%" if f.get("discount_annual") is not None else "-",
            _fmt(f.get("remaining_years")) if f.get("remaining_years") is not None else "-",
            _date_str(f.get("maturity_date")),
            f.get("fund_type", ""),
        ] for f in funds],
    )

    issue_html = _render_table(
        ["股票代码", "正股", "转债", "规模(亿)", "进度", "百元含权", "每股配售", "市场", "一手党股数", "获配张数", "资金(元)"],
        [[
            i.get("stock_code", ""), i.get("stock_name", ""), i.get("bond_name", ""),
            _fmt(i.get("issue_size")) if i.get("issue_size") is not None else "-",
            i.get("progress_name", ""),
            _fmt(i.get("per100_value")) if i.get("per100_value") is not None else "-",
            _fmt(i.get("per_share")) if i.get("per_share") is not None else "-",
            {"sh": "沪", "sz": "深", "bj": "京"}.get(i.get("market"), i.get("market") or ""),
            i.get("min_lot_shares", "-"),
            i.get("min_lot_bonds", "-"),
            f"{i.get('min_lot_capital'):.0f}" if i.get("min_lot_capital") else "-",
        ] for i in issues],
    )

    now = report["generated_at"]
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>金融提醒看板</title>
<style>
  :root { --bg:#f8f9fa; --card:#fff; --text:#212529; --muted:#6c757d; --border:#dee2e6; --accent:#2563eb; --blue-l:#dbeafe; --red:#dc2626; --green:#16a34a; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }
  .container { max-width:1140px; margin:0 auto; padding:16px 20px 48px; }
  header { background:var(--card); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:10; }
  header .inner { max-width:1140px; margin:0 auto; display:flex; align-items:center; justify-content:space-between; padding:0 20px; height:52px; }
  header .brand { font-weight:700; font-size:16px; }
  header .ts { color:var(--muted); font-size:12px; }
  h1 { font-size:20px; margin:18px 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:18px; margin-bottom:18px; overflow-x:auto; }
  .card h2 { font-size:15px; margin-bottom:12px; }
  .badge { background:var(--blue-l); color:var(--accent); border-radius:12px; padding:2px 8px; font-size:12px; margin-left:6px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { padding:8px 12px; text-align:left; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { background:var(--blue-l); color:var(--accent); font-weight:600; }
  tr:hover { background:#f1f5f9; }
  .red { color:var(--red); } .green { color:var(--green); }
  nav a { margin-right:14px; font-size:13px; color:var(--accent); text-decoration:none; font-weight:500; }
  .empty { color:var(--muted); padding:24px; text-align:center; }
  @media(max-width:768px) { th,td { padding:6px 8px; font-size:12px; } }
</style>
</head>
<body>
<header>
  <div class="inner">
    <span class="brand">金融提醒看板</span>
    <span class="ts">数据时间：__NOW__</span>
  </div>
</header>
<div class="container">
  <nav>
    <a href="#cb">可转债轮动</a>
    <a href="#funds">封闭基金</a>
    <a href="#issues">待发可转债(抢权)</a>
  </nav>

  <section id="cb">
    <h1>可转债轮动策略</h1>
    <p class="sub">东财全市场行情 · 每小时自动刷新</p>
    __STRATEGIES__
  </section>

  <section id="funds">
    <h1>封闭基金折价</h1>
    <p class="sub">集思录实时数据（游客仅前20条，完整需 cookie）</p>
    <div class="card">__FUNDS__</div>
  </section>

  <section id="issues">
    <h1>待发可转债 · 抢权</h1>
    <p class="sub">东财数据中心</p>
    <div class="card">__ISSUES__</div>
  </section>

  <p class="sub" style="margin-top:24px;text-align:center">由 GitHub Actions 定时生成 · 仅供学习参考，不构成投资建议</p>
</div>
</body>
</html>"""

    html = (html
            .replace("__NOW__", now)
            .replace("__STRATEGIES__", strategy_html or "<div class='empty'>暂无策略数据</div>")
            .replace("__FUNDS__", fund_html or "<div class='empty'>暂无基金数据</div>")
            .replace("__ISSUES__", issue_html or "<div class='empty'>暂无待发可转债</div>"))

    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # ---------- 5) 数据快照（供推送 / 历史） ----------
    snapshot = {
        "generated_at": now,
        "cb": merged if live_data else [],
        "funds": [
            {**f, "maturity_date": _date_str(f.get("maturity_date"))}
            for f in funds
        ],
        "issues": issues,
        "strategies": {
            k: [b for b in v["bonds"][:20]]
            for k, v in strategies.items()
        } if strategies else {},
    }
    with open(os.path.join(DIST, "data.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1, default=str)

    print(f"[sitegen] 完成，耗时 {time.time()-t0:.0f}s，"
          f"转债 {report['sections']['cb_count']}，基金 {report['sections']['fund_count']}，"
          f"待发 {report['sections']['issue_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())