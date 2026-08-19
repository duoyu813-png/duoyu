"""GitHub Actions 定时推送（PushPlus 微信，zh-CN）

用法：
  python notify.py --test           手动测试推送
  python notify.py --discount       每天 20:00(北京) 封闭基金折价推送
  python notify.py --rotation       每周五 14:00(北京) 可转债轮动推送
  python notify.py --issues         待发可转债状态变动推送

说明：
  - 云端无数据库/无关注列表，折价推送改为推送「折价最深的 Top10」（游客 20 条内）；
    待发转债推送为「与上次快照相比新增/变更状态」的条目，快照存于 last_issues.json。
  - 轮动推送的 last_strategies.json 随仓库维护，用于计算轮入/轮出。
"""
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from config import Config

API_BASE = "http://www.pushplus.plus"
BASE = os.path.dirname(os.path.abspath(__file__))
LAST_STRATEGIES = os.path.join(BASE, "last_strategies.json")
LAST_ISSUES = os.path.join(BASE, "last_issues.json")


def _fmt(v, nd=2):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


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
    funds.sort(key=lambda f: (f.get("discount_rate") is None, f.get("discount_rate") or 0))
    top = [f for f in funds if f.get("discount_rate") is not None][:10]
    lines = ["## 封闭基金折价（Top10）\n"]
    for f in top:
        lines.append(
            f"- **{f.get('name','-')}** | 折价:{_fmt(f.get('discount_rate'))}%"
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

    lines = ["## 周五可转债轮动提醒\n"]
    current = {}
    has_change = False
    for key, data in results.items():
        name = data["name"]
        bonds = data["bonds"][:10]
        current[key] = [
            {"code": b.get("code"), "name": b.get("name"),
             "price": _fmt(b.get("price")), "premium_rate": _fmt(b.get("premium_rate"))}
            for b in bonds
        ]
        last_bonds = last.get(key, [])
        last_map = {b.get("code"): b for b in last_bonds}
        cur_map = {b.get("code"): b for b in current[key]}
        new_in = [b for b in current[key] if b.get("code") and b["code"] not in last_map]
        old_out = [b for c, b in last_map.items() if c not in cur_map]
        if not new_in and not old_out:
            continue
        has_change = True
        lines.append(f"\n### {name}")
        if new_in:
            lines.append("🟢 轮入:")
            for b in new_in:
                lines.append(f"  {b.get('name','')} | {b.get('price','-')}元 | 溢价{b.get('premium_rate','-')}%")
        if old_out:
            lines.append("🔴 轮出:")
            for b in old_out[:5]:
                lines.append(f"  {b.get('name','')} | {b.get('price','-')}元")
    _save(LAST_STRATEGIES, current)
    if not has_change:
        lines.append("\n本期各策略无变动")
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
    else:
        ok = _send("金融提醒工具测试",
                   f"## 测试消息\n\n如果收到说明推送正常\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sys.exit(0 if ok else 1)