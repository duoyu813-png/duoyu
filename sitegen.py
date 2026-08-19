"""GitHub Actions 静态看板生成器（无 Flask / 无数据库）· 多页面版本

页面结构：
  index.html               首页（三个板块入口卡片）
  cb.html                  可转债轮动（策略按钮）
  cb_<key>.html            单个策略详情（全部标的 + 排名）
  issues.html              待发可转债 · 抢权（按进度分组的按钮）
  issues_<n>.html          单个进度分组详情
  funds.html               封闭基金折价 Top20（含"查看全部"）
  funds_all.html           封闭基金全部
  data.json                数据快照（供推送 / 历史）
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
from strategies.cb_strategies import run_all_strategies, ALL_STRATEGIES

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
BASE = os.path.dirname(os.path.abspath(__file__))

CSS = """<style>
  :root { --bg:#f8f9fa; --card:#fff; --text:#212529; --muted:#6c757d; --border:#dee2e6; --accent:#2563eb; --blue-l:#dbeafe; --red:#dc2626; --green:#16a34a; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }
  .container { max-width:1080px; margin:0 auto; padding:14px 16px 48px; }
  header { background:var(--card); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:10; }
  header .inner { max-width:1080px; margin:0 auto; display:flex; align-items:center; justify-content:space-between; padding:0 16px; height:52px; }
  header .brand { font-weight:700; font-size:16px; }
  header .ts { color:var(--muted); font-size:12px; }
  h1 { font-size:20px; margin:16px 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:16px; overflow-x:auto; }
  .card h2 { font-size:15px; margin-bottom:12px; }
  .badge { background:var(--blue-l); color:var(--accent); border-radius:12px; padding:2px 8px; font-size:12px; margin-left:6px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { padding:7px 10px; text-align:left; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { background:var(--blue-l); color:var(--accent); font-weight:600; }
  tr:hover { background:#f1f5f9; }
  .red { color:var(--red); } .green { color:var(--green); }
  .rank-tag { color:var(--red); font-weight:700; font-size:11px; vertical-align:top; margin-right:2px; }
  .back { display:inline-block; margin:8px 0 16px; font-size:13px; color:var(--accent); text-decoration:none; font-weight:500; }
  .back:hover { text-decoration:underline; }
  .mtop { margin-top:16px; }
  .empty { color:var(--muted); padding:24px; text-align:center; }
  .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }
  .chip { padding:7px 16px; border-radius:20px; font-size:13px; text-decoration:none; border:1px solid var(--border); background:var(--card); color:var(--text-secondary, #495057); transition:all .15s; }
  .chip:hover { border-color:var(--accent); color:var(--accent); }
  .chip.active { background:var(--accent); color:#fff; border-color:var(--accent); }
  .chip .n { font-size:11px; opacity:.8; margin-left:4px; }
  .btn { display:inline-block; padding:8px 18px; border-radius:8px; text-decoration:none; font-size:13px; font-weight:600; text-align:center; }
  .btn-primary { background:var(--accent); color:#fff; }
  .btn-outline { background:var(--card); color:var(--accent); border:1px solid var(--accent); }
  .hero { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; margin-top:8px; }
  .hero-card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; text-decoration:none; color:var(--text); transition:all .15s; display:block; }
  .hero-card:hover { border-color:var(--accent); box-shadow:0 2px 10px rgba(37,99,235,.08); }
  .hero-card h3 { font-size:16px; margin-bottom:6px; color:var(--accent); }
  .hero-card p { font-size:13px; color:var(--muted); }
  .hero-card .cnt { font-size:12px; color:var(--muted); margin-top:10px; }
  nav.breadcrumb a { margin-right:12px; font-size:13px; color:var(--accent); text-decoration:none; font-weight:500; }
  td { word-break:break-all; }
  .badge { background:var(--blue-l); color:var(--accent); border-radius:12px; padding:2px 8px; font-size:12px; margin-left:6px; }
  @media(max-width:768px) { th,td { padding:6px 7px; font-size:12px; } }
</style>"""


# 板块主题色（首页四卡片与各页面主色调一致）
THEME = {
    "cb":      {"accent": "#dc2626", "blue": "#fee2e2", "name": "可转债轮动"},        # 红
    "funds":   {"accent": "#ea580c", "blue": "#ffedd5", "name": "封闭基金折价"},      # 橙
    "issues":  {"accent": "#2563eb", "blue": "#dbeafe", "name": "待发可转债(抢权)"},  # 蓝
    "qiangquan": {"accent": "#16a34a", "blue": "#dcfce7", "name": "抢权评分看板"},   # 绿
}


def _page(title, body, now, theme=None):
    """通用页面外壳，含顶部导航与主题色"""
    accent = (THEME[theme]["accent"] if theme and theme in THEME else "#2563eb")
    blue = (THEME[theme]["blue"] if theme and theme in THEME else "#dbeafe")
    style_ov = f"<style>:root{{--accent:{accent};--blue-l:{blue};}}</style>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} · 小渔点儿</title>
{CSS}
{style_ov}
</head>
<body>
<header>
  <div class="inner">
    <a href="index.html" style="text-decoration:none;color:var(--text);"><span class="brand">小渔点儿</span></a>
    <span class="ts">数据时间：{now}</span>
  </div>
</header>
<div class="container">
  <nav class="breadcrumb"><a href="index.html">首页</a></nav>
{body}
  <p class="sub" style="margin-top:24px;text-align:center">由 GitHub Actions 定时生成 · 仅供学习参考，不构成投资建议</p>
</div>
</body>
</html>"""


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


def _short_concept(concept, n=2):
    """概念只保留前 n 个（逗号分隔），适配手机阅读"""
    if not concept:
        return ""
    parts = [p.strip() for p in str(concept).split(",") if p.strip()]
    return ",".join(parts[:n] or [""])


def _code_with_rank(code, rank):
    """排名在 1/5/10/15/20 时，代码左上角加红色数字标注"""
    if rank in (1, 5, 10, 15, 20):
        return f"<span class='rank-tag'>{rank}</span>{code}"
    return str(code)


def fetch_funds_merged():
    """封闭基金：两源融合，返回全量列表（无需集思录 cookie）

    集思录（官方封基/定开，字段全：折价年化/剩余年限/到期日/类型），游客仅前 20 条；
    东财 MK0404/0405 板块（全市场场内基金 150+ 条，含现价/净值/折价/上市日）。
    以东财为全集主体，集思录覆盖富集字段。
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
        merged = {
            "code": code,
            "name": f.get("name") or (j or {}).get("name", ""),
            "price": f.get("price") if f.get("price") is not None else (j or {}).get("price"),
            "change_pct": f.get("change_pct"),
            "nav": f.get("nav") if f.get("nav") is not None else (j or {}).get("nav"),
            "discount_rate": f.get("discount_rate") if f.get("discount_rate") is not None else (j or {}).get("discount_rate"),
            "discount_annual": (j or {}).get("discount_annual"),
            "remaining_years": (j or {}).get("remaining_years"),
            "maturity_date": (j or {}).get("maturity_date") or f.get("expire_date"),
            "last_volume": (j or {}).get("last_volume") if not None else f.get("amount"),
            "total_cap": f.get("total_cap"),
            "fund_type": (j or {}).get("fund_type") or ("定开" if (j or {}).get("notes") else str(f.get("fund_type") or "")),
            "notes": (j or {}).get("notes", ""),
        }
        out.append(merged)
    ji_codes = set(ji_map.keys())
    em_codes = {f.get("code") for f in (em or [])}
    for code in ji_codes - em_codes:
        j = ji_map[code]
        out.append(dict(j))
    # 折价率从高到低（折价幅度越大越靠前；折价率无值排最后）
    out.sort(key=lambda f: (f.get("discount_rate") is None, -(f.get("discount_rate") or 0)))
    return out


CB_HEADERS = ["代码", "名称", "价格", "溢价%", "剩余规模(亿)", "税前YTM%", "概念"]


def _cb_rows(bonds, limit=None):
    rows = []
    for rank, b in enumerate(bonds, start=1):
        if limit and rank > limit:
            break
        rows.append([
            _code_with_rank(b.get("code", ""), rank), b.get("name", ""),
            _fmt(b.get("price")),
            _fmt(b.get("premium_rate")) + "%" if b.get("premium_rate") is not None else "-",
            _fmt(b.get("remaining_size")) if b.get("remaining_size") is not None else "-",
            _fmt(b.get("ytm_before_tax")) + "%" if b.get("ytm_before_tax") is not None else "-",
            _short_concept(b.get("concept")),
        ])
    return rows


def main() -> int:
    t0 = time.time()
    print("[sitegen] 开始生成静态看板（多页面）")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = {"generated_at": now, "sections": {}}

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
        cb_count = len(merged)
    else:
        strategies = {}
        cb_count = 0

    # ---------- 2) 封闭基金 ----------
    funds = fetch_funds_merged()
    fund_count = len(funds)

    # ---------- 3) 待发可转债（抢权）计数：优先取抢权数据，兜底用东财 ----------
    issue_count = 0
    for cand in (os.path.join(DIST, "qiangquan.json"),
                 os.path.join(BASE, "hanquan", "last_qiangquan.json")):
        try:
            if os.path.exists(cand):
                with open(cand, "r", encoding="utf-8") as f:
                    qdata = json.load(f)
                issue_count = int(qdata.get("total") or 0)
                if issue_count:
                    break
        except Exception:
            continue
    if issue_count == 0:
        all_issues = EastMoneyScraper.fetch_new_cb_issues()
        issue_count = len([i for i in all_issues if i.get("progress_name") != "已申购待上市"])
    report["sections"]["issue_count"] = issue_count

    os.makedirs(DIST, exist_ok=True)

# ---------- 首页 index.html ----------
    cb_total = sum(len(d["bonds"]) for d in strategies.values())
    hero_cards = []

    def _hero(href, color, bcolor, title, desc, cnt):
        return f"""
  <a class="hero-card" href="{href}" style="--accent:{color};--blue-l:{bcolor};">
    <h3>{title}</h3>
    <p>{desc}</p>
    <p class="cnt">{cnt}</p>
  </a>"""

    hero_cards.append(_hero(
        "cb.html", "#dc2626", "#fee2e2",
        "可转债轮动", "七个策略看板：130三低 / 双低 / 高收益等",
        f"{len(ALL_STRATEGIES)} 个策略 · 覆盖 {cb_count} 只转债"))
    hero_cards.append(_hero(
        "funds.html", "#ea580c", "#ffedd5",
        "封闭基金折价", "集思录 + 东财融合，折价率前 20 + 查看全部",
        f"{fund_count} 只基金"))
    hero_cards.append(_hero(
        "issues.html", "#2563eb", "#dbeafe",
        "待发可转债(抢权)", '按"同意注册 / 待申购 / 待发行"进度分组浏览',
        f"{issue_count} 条待发债"))
    hero_cards.append(_hero(
        "qiangquan.html", "#16a34a", "#dcfce7",
        "可转债抢权·评分看板", "全市场待发债评分体系：含权量/隐形流通/业绩/操作建议",
        "逐只评分 · 四版回测体系"))
    hero = f"<div class=\"hero\">{''.join(hero_cards)}</div>"
    body_home = f"""
<h1>小渔点儿</h1>
<p class="sub">自动抓取东财全市场行情 · 定时推送微信 · 数据每小时刷新</p>
{hero}
"""
    write_page("index.html", "首页", body_home, now)

    # ---------- 可转债轮动 cb.html（策略按钮 + 默认展示高到期收益率） ----------
    chips = []
    for key in ALL_STRATEGIES:
        d = strategies.get(key)
        n = len(d["bonds"]) if d else 0
        is_active = ' active' if key == "high_ytm" else ''
        chips.append(f"<a class='chip{is_active}' href='cb_{key}.html'>{ALL_STRATEGIES[key][0]}<span class='n'>{n}</span></a>")
    # 默认展开"高到期收益率"榜单
    default_key = "high_ytm"
    default_table = ""
    if default_key in strategies and strategies[default_key]["bonds"]:
        d = strategies[default_key]
        default_table = f"""
<div class="card">
  <h2>高到期收益率 <span class="badge">{len(d['bonds'])}</span> <span style="font-size:12px;color:var(--muted)">(默认展示)</span></h2>
  {_render_table(CB_HEADERS, _cb_rows(d["bonds"], limit=20))}
</div>"""
    body_cb = f"""
<a class="back" href="index.html">← 返回首页</a>
<h1>可转债轮动策略</h1>
<p class="sub">选择策略查看完整榜单（每策略最多展示 20 只，排名 1/5/10/15/20 用红色标注）</p>
<div class="filters">{''.join(chips)}</div>
{default_table}
"""
    write_page("cb.html", "可转债轮动", body_cb, now, theme="cb")

    # ---------- 各策略详情 cb_<key>.html ----------
    for key, (name, _) in ALL_STRATEGIES.items():
        d = strategies.get(key)
        bonds_list = d["bonds"] if d else []
        if not bonds_list:
            content = "<div class='empty'>暂无数据</div>"
        else:
            content = f"""
<div class="card">
  <h2>{name} <span class="badge">{len(bonds_list)}</span></h2>
  {_render_table(CB_HEADERS, _cb_rows(bonds_list, limit=20))}
</div>"""
        body = f"""
<a class="back" href="cb.html">← 返回策略列表</a>
<h1>{name}</h1>
<p class="sub">按评分从低到高排序 · 展示前 20 名</p>
{content}
"""
        write_page(f"cb_{key}.html", name, body, now, theme="cb")

    # ---------- 待发可转债 issues.html ----------
    # 说明：完整抢权看板页面由 qiangquan_gen.py 生成（按审核进度分组 + 排序 + 搜索），
    # 这里仅生成首页占位卡片，避免 sitegen 运行时 qiangquan 数据尚未生成导致页面为空。
    issue_note = "抢权详情页由独立流程生成，若此处为空请稍后刷新（qiangquan 数据生成中）"
    body_iss = f"""
<a class="back" href="index.html">← 返回首页</a>
<h1>待发可转债 · 抢权</h1>
<p class="sub">{issue_note} · 当前共 {issue_count} 条待发</p>
"""
    write_page("issues.html", "待发可转债", body_iss, now, theme="issues")

    # ---------- 封闭基金 funds.html（Top20 + 查看全部） ----------
    fund_rows = [[
        f.get("code", ""), f.get("name", ""),
        _fmt(f.get("price")),
        _fmt(f.get("change_pct")) + "%" if f.get("change_pct") is not None else "-",
        _fmt(f.get("nav")),
        _fmt(f.get("discount_rate")) + "%" if f.get("discount_rate") is not None else "-",
        _fmt(f.get("discount_annual")) + "%" if f.get("discount_annual") is not None else "-",
        _fmt(f.get("remaining_years")) if f.get("remaining_years") is not None else "-",
        _date_str(f.get("maturity_date")),
        f.get("fund_type", ""),
    ] for f in funds]

    if fund_rows:
        top20 = _render_table(["代码", "名称", "现价", "涨跌%", "净值", "折价%", "折价年化%", "剩余年限", "到期日", "类型"], fund_rows[:20])
        butt = f"<div class='filters'><a class='chip' href='funds_all.html'>查看全部 {len(fund_rows)} 只 →</a></div>"
    else:
        top20 = "<div class='empty'>暂无数据</div>"
        butt = ""
    body_fund = f"""
<a class="back" href="index.html">← 返回首页</a>
<h1>封闭基金折价</h1>
<p class="sub">折价率从高到低 · 展示前 20 名（折价越深越靠前）</p>
{butt}
<div class="card">{top20}</div>
"""
    write_page("funds.html", "封闭基金", body_fund, now, theme="funds")

    # ---------- 封闭基金全部 funds_all.html ----------
    if fund_rows:
        all_table = _render_table(["代码", "名称", "现价", "涨跌%", "净值", "折价%", "折价年化%", "剩余年限", "到期日", "类型"], fund_rows)
    else:
        all_table = "<div class='empty'>暂无数据</div>"
    body_fund_all = f"""
<a class="back" href="funds.html">← 返回 Top20</a>
<h1>全部封闭基金</h1>
<p class="sub">共 {len(fund_rows)} 只 · 折价率从高到低</p>
<div class="card">{all_table}</div>
"""
    write_page("funds_all.html", "全部封闭基金", body_fund_all, now, theme="funds")

    # ---------- 数据快照（供推送 / 历史） ----------
    snapshot = {
        "generated_at": now,
        "cb": merged if live_data else [],
        "funds": [
            {**f, "maturity_date": _date_str(f.get("maturity_date"))}
            for f in funds
        ],
        "issues": [],
        "strategies": {
            k: [b for b in v["bonds"][:20]]
            for k, v in strategies.items()
        } if strategies else {},
    }
    with open(os.path.join(DIST, "data.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1, default=str)

    print(f"[sitegen] 完成，耗时 {time.time()-t0:.0f}s，"
          f"转债 {cb_count}，基金 {fund_count}，待发 {issue_count}")
    return 0


def write_page(filename, title, body, now, theme=None):
    with open(os.path.join(DIST, filename), "w", encoding="utf-8") as f:
        f.write(_page(title, body, now, theme=theme))


if __name__ == "__main__":
    sys.exit(main())