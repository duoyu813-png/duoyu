"""GitHub Actions 定时推送（PushPlus 微信，zh-CN）

用法：
  python notify.py --test           手动测试推送
  python notify.py --discount       每天 20:00(北京) 封闭基金折价推送
  python notify.py --rotation       每周五 14:00(北京) 可转债轮动推送
  python notify.py --issues         待发可转债状态变动推送
  python notify.py --cb-alert       低价/低溢价「变动」推送（当天新进入价格<110 或 溢价<10% 的转债）

说明：
  - 云端无数据库/无关注列表，折价推送改为推送「折价最深的 Top10」（游客 20 条内）；
    待发转债推送为「与上次快照相比新增/变更状态」的条目，快照存于 last_issues.json。
  - 轮动推送的 last_strategies.json 随仓库维护，用于计算轮入/轮出。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from config import Config

API_BASE = "http://www.pushplus.plus"
BASE = os.path.dirname(os.path.abspath(__file__))
LAST_STRATEGIES = os.path.join(BASE, "last_strategies.json")
LAST_ISSUES = os.path.join(BASE, "last_issues.json")
LAST_CB_ALERTS = os.path.join(BASE, "last_cb_alerts.json")


def _to_num(v):
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v, nd=2):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


_ROTATION_TOP_N = 20


def _md_table(rows):
    """rows: [{name, code, price, premium_rate}] -> markdown 表格（PushPlus markdown 渲染）"""
    if not rows:
        return ["（无）"]
    out = ["| 名称 | 现价 | 溢价率 |", "| --- | ---: | ---: |"]
    for r in rows:
        name = str(r.get("name") or "-")
        code = str(r.get("code") or "-")
        price = str(r.get("price") or "-")
        prem = str(r.get("premium_rate") or "-")
        out.append(f"| {name}（{code}） | {price}元 | {prem}% |")
    return out


def _build_rotation_message(results, last):
    """按每个策略前 N(=20, 不足按实际)只作为轮动池，
    对比上次快照生成 markdown：每个策略同时给出 轮入 与 轮出 表格。
    返回 (message_lines, 最新快照)。"""
    top_n = _ROTATION_TOP_N
    lines = ["## 周五可转债轮动",
             f"轮动池：每个策略排名前 {top_n} 只（不足按实际只数）\n"]
    current = {}
    changed_any = False
    for key, data in (results or {}).items():
        name = data.get("name") or key
        bonds = data.get("bonds") or []
        if not bonds:
            continue
        base = bonds[:top_n]

        prev = last.get(key) or []
        prev_map = {str(b.get("code")): b for b in prev if b.get("code")}

        cur_rows = []
        for b in base:
            cur_rows.append({
                "code": str(b.get("code") or ""),
                "name": b.get("name"),
                "price": _fmt(b.get("price")),
                "premium_rate": _fmt(b.get("premium_rate")),
            })
        current[key] = cur_rows

        cur_codes = {r["code"] for r in cur_rows if r["code"]}
        prev_codes = set(prev_map.keys())
        in_codes = [c for c in cur_codes if c not in prev_codes]
        out_codes = [c for c in prev_codes if c not in cur_codes]

        # 全量索引：轮出债通常仍存在（排名跌出前20），用其最新价格展示
        all_map = {}
        for b in bonds:
            code = str(b.get("code") or "")
            if code:
                all_map[code] = {"name": b.get("name"),
                                 "price": _fmt(b.get("price")),
                                 "premium_rate": _fmt(b.get("premium_rate"))}

        in_rows = [r for r in cur_rows if r["code"] in in_codes]
        out_rows = []
        for c in out_codes:
            src = all_map.get(c) or prev_map.get(c) or {}
            out_rows.append({"code": c, "name": src.get("name"),
                             "price": src.get("price"), "premium_rate": src.get("premium_rate")})

        if not in_rows and not out_rows:
            continue
        changed_any = True
        lines.append(f"\n### {name}（池 {len(base)} 只）")
        lines.append("\n🟢 **轮入**")
        lines += _md_table(in_rows)
        lines.append("\n🔴 **轮出**")
        lines += _md_table(out_rows)

    if not changed_any:
        lines.append("\n本期各策略轮动池均无变动。")
    return lines, current


def _send(title, content):
    token = Config.PUSHPLUS_TOKEN
    if not token:
        print("[notify] PUSHPLUS_TOKEN 未配置，跳过推送")
        return False
    payload = {
        "token": token,
        "title": title or "消息提醒",
        "content": content,
        "template": "markdown",
        "channel": "wechat",
    }
    try:
        r = requests.post(f"{API_BASE}/send", json=payload, timeout=15)
        data = r.json()
        ok = data.get("code") == 200
        print(f"[notify] 推送{'成功' if ok else '失败'}: {data}")
        return ok
    except Exception as e:
        print(f"[notify] 请求失败: {e}")
        return False


def _load(existing, fresh, key):
    last = {}
    if os.path.exists(existing):
        try:
            with open(existing, "r", encoding="utf-8") as f:
                last = json.load(f)
        except Exception:
            pass
    _save(existing, fresh)
    return last


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    except Exception as e:
        print(f"[notify] 保存 {path} 失败: {e}")


def fetch_funds():
    from sitegen import fetch_funds_merged
    return fetch_funds_merged()


def fetch_strategies():
    from scrapers.eastmoney import EastMoneyScraper
    from scrapers.data_merger import merge_cb_data
    from strategies.cb_strategies import run_all_strategies
    from sitegen import _build_bond_dicts
    live = EastMoneyScraper.fetch_cb_list()
    if not live:
        return {}
    fundamentals = EastMoneyScraper.fetch_cb_fundamentals()
    codes = [b["stock_code"] for b in live if b.get("stock_code")]
    concepts = EastMoneyScraper.fetch_stock_concepts(codes)
    fin = EastMoneyScraper.fetch_stock_financials(codes)
    merged = merge_cb_data(live, fundamentals, concepts, fin)
    return run_all_strategies(_build_bond_dicts(merged))


def push_discount():
    funds = fetch_funds()
    if not funds:
        print("[notify] 无基金数据，跳过折价推送")
        return False
    # 仅保留有折价（折价率>0）的基金，并按折价从高到低排列（折价幅度越大越靠前）；
    # 原实现按升序会把溢价(负值)/折价小的排在前面，导致推送内容看起来像"溢价"。
    dis = [f for f in funds if f.get("discount_rate") is not None and f.get("discount_rate") > 0]
    dis.sort(key=lambda f: f.get("discount_rate") or 0, reverse=True)
    top = dis[:10]
    lines = ["## 封闭基金折价（Top10）\n",
             "按折价从高到低（折价率>0 为折价）\n"]
    for f in top:
        lines.append(
            f"- **{f.get('name','-')}** ({f.get('code','-')}) | 折价:{_fmt(f.get('discount_rate'))}%"
            f" | 现价:{_fmt(f.get('price'))} | 净值:{_fmt(f.get('nav'))}"
            f" | 到期:{f.get('maturity_date') or '-'}"
        )
    return _send("封闭基金折价提醒", "\n".join(lines))


def push_rotation():
    results = fetch_strategies()
    if not results:
        print("[notify] 无策略数据，跳过轮动推送")
        return False
    last = {}
    if os.path.exists(LAST_STRATEGIES):
        try:
            with open(LAST_STRATEGIES, "r", encoding="utf-8") as f:
                last = json.load(f)
        except Exception:
            last = {}

    lines, current = _build_rotation_message(results, last)
    _save(LAST_STRATEGIES, current)
    return _send("周五可转债轮动", "\n".join(lines))


def push_issues():
    from scrapers.eastmoney import EastMoneyScraper
    issues = EastMoneyScraper.fetch_new_cb_issues()
    if not issues:
        print("[notify] 暂无待发可转债，跳过")
        return False
    last = {}
    if os.path.exists(LAST_ISSUES):
        try:
            with open(LAST_ISSUES, "r", encoding="utf-8") as f:
                last = json.load(f)
        except Exception:
            last = {}
    cur = {i.get("bond_code") or i.get("bond_name"): i for i in issues}
    changes = []
    for code, item in cur.items():
        old = last.get(code)
        if old is None:
            changes.append((item.get("bond_name","?"), "新增", item.get("progress_name","")))
        elif str(old.get("progress_name","")) != str(item.get("progress_name","")):
            changes.append((item.get("bond_name","?"),
                            f"{old.get('progress_name','')}→{item.get('progress_name','')}", ""))
    _save(LAST_ISSUES, cur)
    if not changes:
        print("[notify] 待发转债无变动")
        return False
    lines = ["## 可转债发行状态变动\n"]
    for name, status, extra in changes[:10]:
        lines.append(f"- **{name}** {status} {extra}")
    return _send("可转债发行变动", "\n".join(lines))


def _is_trading_window():
    """A股交易时段（北京周一~周五 09:30-15:10），可转债低价/低溢价推送仅在此时节流触发"""
    now = datetime.utcnow() + timedelta(hours=8)  # 北京墙钟
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 930 <= hm <= 1510


def push_cb_alert():
    """推送「当天新进入」低价/低溢价条件的可转债变动提醒（价格<110 或 转股溢价率<10%）。

    与上一次检查的命中集合比对，仅推送本次「新增命中」的转债；
    同一只债当天只提醒一次；不再是完整版列表。
    状态文件 last_cb_alerts.json：{"set": [命中代码...], "pushed": {code: 日期}}
    """
    from scrapers.eastmoney import EastMoneyScraper

    force = os.environ.get("CB_ALERT_FORCE", "").lower() in ("1", "true", "yes", "y", "on")
    if not _is_trading_window() and not force:
        print("[notify] 非交易时段，跳过低价/低溢价推送（如需手动测试请设置 CB_ALERT_FORCE=1）")
        return False

    live = EastMoneyScraper.fetch_cb_list()
    if not live:
        print("[notify] 无可转债数据，跳过低价/低溢价推送")
        return False

    today = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")
    # 当前命中集合（需有真实成交价）
    current = {}
    for b in live:
        price = _to_num(b.get("price"))
        if price is None:
            # 无成交价（当日停牌/未成交/未上市）：价格是 "-"，溢价为推算值不可靠，跳过
            continue
        prem = _to_num(b.get("premium_rate"))
        reasons = []
        if price < 110:
            reasons.append("价格<110")
        if prem is not None and prem < 10:
            reasons.append("溢价<10%")
        if not reasons:
            continue
        code = b.get("code", "")
        current[code] = {
            "code": code, "name": b.get("name", ""),
            "price": price, "premium_rate": prem, "reasons": reasons,
        }

    state = {"set": [], "pushed": {}}
    if os.path.exists(LAST_CB_ALERTS):
        try:
            with open(LAST_CB_ALERTS, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {"set": [], "pushed": {}}
    if not isinstance(state, dict) or "set" not in state:
        # 兼容旧格式 {code: date}：视为“上次已命中且当日已推送”
        old = state if isinstance(state, dict) else {}
        state = {"set": sorted(str(k) for k in old.keys()),
                 "pushed": {str(k): str(v) for k, v in old.items()}}
    prev_set = set(state.get("set") or [])
    pushed = state.get("pushed") or {}

    # 仅推送“新增命中 + 当天未推送过”
    new_hits = []
    for code, info in current.items():
        if pushed.get(code) == today:
            continue
        if code not in prev_set:
            new_hits.append(info)

    if new_hits:
        new_hits.sort(key=lambda h: (h["premium_rate"] is None, h["premium_rate"] or 0))
        lines = ["## 可转债低价/低溢价变动\n",
                 f"今日新增 {len(new_hits)} 只（价格<110 或 转股溢价率<10%）\n"]
        for h in new_hits[:25]:
            parts = [f"**{h['name']}** ({h['code']})"]
            if h["price"] is not None:
                parts.append(f"{_fmt(h['price'])}元")
            if h["premium_rate"] is not None:
                parts.append(f"溢价{_fmt(h['premium_rate'])}%")
            parts.append("/".join(h["reasons"]))
            lines.append("- " + " | ".join(parts))
        _send("可转债低价/低溢价变动提醒", "\n".join(lines))

    # 更新状态：命中集合 + 当日处理记录（同一只债当天不再重复提醒）
    for code in current:
        pushed[code] = today
    state["set"] = sorted(current.keys())
    state["pushed"] = pushed
    _save(LAST_CB_ALERTS, state)

    return bool(new_hits)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--test"
    t0 = time.time()
    ok = False
    if mode == "--discount":
        ok = push_discount()
    elif mode == "--rotation":
        ok = push_rotation()
    elif mode == "--issues":
        ok = push_issues()
    elif mode == "--cb-alert":
        ok = push_cb_alert()
    else:
        ok = _send("金融提醒工具测试",
                   f"## 测试消息\n\n如果收到说明推送正常\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sys.exit(0 if ok else 1)