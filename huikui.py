"""股东回馈活动 · 命令行入口

用法：
  python huikui.py scan        多源扫描 -> 新命中自动解析 -> 微信推送 -> 重新生成页面
                               数据源：东方财富公告 + 巨潮资讯全文检索 + 搜狗微信关键词检索
  python huikui.py backfill    加大扫描页数补历史（东财列表接口可达的近月窗口）
  python huikui.py gen         仅用本地快照+种子数据重新生成页面（无网络）
  python huikui.py test <art_code> [code] [title]
                               用指定公告 art_code 实测解析效果（本地验证用）

数据存储：
  huikui/last_events.json   持久快照（随仓库维护）：{events:[...], scan:{last_date}}
  huikui/seeds.json         手动补充的事件，可把“今年更早、接口窗口外”已知活动放这里，
                            页面会与自动扫描结果合并展示。格式（events 数组元素）：
                            {"code":"000858","name":"五粮液","notice_date":"2026-06-20",
                             "title":"关于开展股东回馈活动的公告","shares":"每持有1000股…",
                             "reward":"回馈内容…","requirement":"股东要求…","link":"公告链接(可选)"}
输出：
  dist/huikui.html          股东回馈活动看板页
"""
import asyncio
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from huikui import scanner, pagegen  # noqa: E402

LAST_EVENTS = os.path.join(BASE, "huikui", "last_events.json")
SEEDS = os.path.join(BASE, "huikui", "seeds.json")

MAX_PUSH_PER_RUN = 5


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _dump(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)


def _norm_seed(e: dict) -> dict:
    """规范化一条手动补充记录：id 可缺省，自动生成。"""
    date = (e.get("notice_date") or "")[:10]
    code = str(e.get("code") or "")
    return {
        "id": str(e.get("id") or f"seed:{date}:{code}"),
        "source": "seed",
        "code": code,
        "name": str(e.get("name") or ""),
        "title": str(e.get("title") or ""),
        "notice_date": date,
        "shares": str(e.get("shares") or ""),
        "reward": str(e.get("reward") or ""),
        "requirement": str(e.get("requirement") or ""),
        "link": str(e.get("link") or ""),
        "pdf": str(e.get("pdf") or ""),
        "created_at": str(e.get("created_at") or ""),
    }


def _all_events(store: dict, seeds: dict) -> list[dict]:
    events = [e for e in (store.get("events") or []) if e.get("id")]
    for s in (seeds.get("events") or []):
        n = _norm_seed(s)
        if not any(e.get("id") == n["id"] for e in events):
            events.append(n)
    return events


def _pair_key(e: dict) -> tuple | None:
    """跨源去重键：同一公司同一天的公告/文章视为同一条活动。"""
    code = str(e.get("code") or "")
    date = (e.get("notice_date") or "")[:10]
    if code and date:
        return (code, date)
    return None


def _push_event(ev: dict) -> bool:
    """PushPlus 推送一条股东回馈活动（标题固定为「多鱼推送—股东回馈活动」）。"""
    try:
        from notify import _send
    except Exception as e:
        print(f"[huikui] 无法导入推送模块: {e}")
        return False
    lines = [
        "## 股东回馈活动",
        f"- **公司**：{ev.get('name') or '-'}（{ev.get('code') or '-'}）",
        f"- **公告时间**：{(ev.get('notice_date') or '')[:10]}",
        f"- **活动标题**：{ev.get('title') or '-'}",
        f"- **股数要求**：{ev.get('shares') or '见公告原文'}",
        f"- **股东要求**：{ev.get('requirement') or '见公告原文'}",
        f"- **回馈内容**：{ev.get('reward') or '见公告原文'}",
        f"- **公告链接**：{ev.get('link') or '-'}",
        "",
        "> 今年全部股东回馈活动见网站「股东回馈活动」板块（自动扫描 + 自动解析，仅供参考）",
    ]
    return _send("股东回馈活动", "\n".join(lines))


def _key_state(store: dict) -> str:
    return json.dumps({"events": store.get("events") or [],
                       "scan": store.get("scan") or {}}, ensure_ascii=False)


def cmd_scan(backfill: bool = False) -> int:
    store = _load(LAST_EVENTS)
    if not isinstance(store, dict) or "events" not in store:
        store = {"events": [], "scan": {}}
    seeds = _load(SEEDS)
    if not isinstance(seeds, dict) or "events" not in seeds:
        seeds = {"events": []}

    cursor = {} if backfill else (store.get("scan") or {})
    page_limit = (scanner.INITIAL_PAGE_LIMIT if backfill
                  else (scanner.SCAN_PAGE_LIMIT if cursor.get("last_date")
                        else scanner.INITIAL_PAGE_LIMIT))

    mode = "回扫(补历史)" if backfill else "增量扫描"
    matched, new_cursor = asyncio.run(scanner.scan_feed(cursor, page_limit=page_limit))
    print(f"[huikui] {mode}完成：标题候选 {len(matched)} 条，游标 last_date={new_cursor.get('last_date') or '-'}")

    known_ids = {str(e.get("id")) for e in (store.get("events") or [])}
    for s in (seeds.get("events") or []):
        known_ids.add(_norm_seed(s)["id"])
    known_keys = {k for k in (_pair_key(e) for e in (store.get("events") or [])) if k}
    new_events: list[dict] = []

    def try_add(ev: dict | None) -> None:
        if not ev or not ev.get("id"):
            return
        eid = str(ev["id"])
        if eid in known_ids:
            return
        pk = _pair_key(ev)
        if pk and pk in known_keys:
            return
        known_ids.add(eid)
        if pk:
            known_keys.add(pk)
        new_events.append(ev)

    # 1) 东方财富全市场公告（主源，正文最全）
    for m in matched:
        if m["art_code"] in known_ids:
            continue
        try:
            try_add(scanner.build_event(m))
        except Exception as e:
            print(f"[huikui] 解析失败 {m['art_code']}: {e}")

    # 2) 巨潮资讯全文检索（覆盖更全、可回补历史；正文由 PDF 解析）
    try:
        cn_candidates = asyncio.run(scanner.scan_cninfo())
    except Exception as e:
        print(f"[huikui] 巨潮扫描失败，跳过: {e}")
        cn_candidates = []
    to_build = []
    for it in cn_candidates:
        if ("cninfo:" + it["announcementId"]) in known_ids:
            continue
        pk = (str(it.get("code") or ""), (it.get("notice_date") or "")[:10])
        if pk[0] and pk[1] and pk in known_keys:
            continue
        to_build.append(it)
    for ev in scanner.build_cninfo_events(to_build):
        try_add(ev)

    # 3) 微信公众号（搜狗微信，尽力而为的兜底源）
    try:
        wx_items = scanner.scan_wechat()
    except Exception as e:
        print(f"[huikui] 微信扫描失败，跳过: {e}")
        wx_items = []
    for it in wx_items:
        try:
            try_add(scanner.build_wechat_event(it))
        except Exception as e:
            print(f"[huikui] 微信解析失败: {e}")

    # 新的在前，推送优先推最近的活动
    new_events.sort(key=lambda e: e.get("notice_date") or "", reverse=True)

    if new_events:
        print(f"[huikui] 新收录 {len(new_events)} 条")
        for ev in new_events:
            print(f"   - {ev['notice_date']} {ev['name']}({ev['code']}) {ev['title'][:50]}")
        store["events"] = (store.get("events") or []) + new_events
        store["scan"] = new_cursor
        for i, ev in enumerate(new_events[:MAX_PUSH_PER_RUN], 1):
            ok = _push_event(ev)
            print(f"[huikui] 推送 {i}/{min(len(new_events), MAX_PUSH_PER_RUN)} -> {'成功' if ok else '跳过'}")
        if len(new_events) > MAX_PUSH_PER_RUN:
            print(f"[huikui] 其余 {len(new_events) - MAX_PUSH_PER_RUN} 条不再逐条推送，见网站板块")
    else:
        store["scan"] = new_cursor

    # 生成页面（即使无新事件也刷新时间戳）
    pagegen.render(_all_events(store, seeds))

    # 快照变化才写盘，避免每个定时任务产生一次空提交
    if _key_state(store) != _key_state(_load(LAST_EVENTS)):
        store["generated_at"] = os.environ.get("RUN_TIME") or _now()
        _dump(LAST_EVENTS, store)
        print("[huikui] 已更新 huikui/last_events.json 快照")
    else:
        print("[huikui] 快照无变化，跳过写盘")
    return 0


def cmd_gen() -> int:
    store = _load(LAST_EVENTS)
    seeds = _load(SEEDS)
    if not isinstance(store, dict):
        store = {"events": []}
    if not isinstance(seeds, dict):
        seeds = {"events": []}
    pagegen.render(_all_events(store, seeds))
    return 0


def cmd_test(art_code: str, code: str = "", title: str = "") -> int:
    if not art_code:
        print("用法: python huikui.py test <art_code> [code] [title]")
        return 2
    if not title:
        title = f"(测试公告 {art_code})"
    item = {"art_code": art_code, "code": code, "name": code,
            "title": title, "notice_date": ""}
    ev = scanner.build_event(item)
    print(json.dumps(ev, ensure_ascii=False, indent=1))
    return 0


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "scan"
    if mode == "scan":
        return cmd_scan(backfill=False)
    if mode == "backfill":
        return cmd_scan(backfill=True)
    if mode == "gen":
        return cmd_gen()
    if mode == "test":
        return cmd_test(*args[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
