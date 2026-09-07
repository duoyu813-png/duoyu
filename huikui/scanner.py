"""股东回馈活动 · 公告扫描与字段解析

数据源：东方财富全市场公告列表（东财 np-anotice-stock 接口）。

设计说明：
  - 东财公告检索接口的 keyword 参数实测不可用（返回未过滤的全市场最新公告），
    因此采用「分页拉全市场公告 + 本地按标题关键词过滤」。
  - 列表接口只保留最近约 5 万条（约一个月），所以模块是增量式的：
    用游标记录「上次已完整扫到的公告日 last_date」，每次从最新页往下扫，
    直到连续遇到若干条 notice_date 严格小于 last_date 的公告，即认为已回到
    上次覆盖过的旧区域，停止。这样每次运行只会处理顶部新增的 1~2 个公告日
    的数据块（每 30 分钟调度一次成本很低）。
  - 命中标题关键词后，再拉取公告详情正文（art_code -> 纯文本正文），
    尽力而为抽取：股数门槛 / 股东要求 / 回馈内容；抽不出的留空，页面显示 "-"。
"""
import asyncio
import re
from datetime import datetime

import httpx

FEED_URL = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
            "?sr=-1&page_size=50&page_index={page}&ann_type=A"
            "&client_source=web&stock_list=&f_node=0&s_node=0")
DETAIL_URL = ("https://np-cnotice-stock.eastmoney.com/api/content/ann"
              "?art_code={art}&client_source=web&page_index=1")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json",
}

# 标题候选：标题需同时含「股东」与下列任一活动词（词序不限，
# 兼容 “股东回馈 / 回馈股东 / 股东感恩回馈 / 感恩股东” 等常见措辞）
_TITLE_ACT = [
    "回馈", "馈", "福利", "专享", "专属", "感恩", "答谢", "礼遇",
    "优惠", "特惠", "特供", "内购", "赠送", "品鉴", "伴手礼", "礼盒", "礼包",
]

# 正文确认关键词：能代表“真的是股东福利/回馈活动”的强词 + 资格词
_BODY_STRONG = ["回馈", "馈", "福利", "专享", "专属", "感恩", "答谢",
                "礼盒", "礼包", "礼品", "内购", "特惠", "赠送", "礼遇"]
_BODY_ELIG = ["股东", "持股", "登记在册", "股权登记日", "收盘后", "在册股东"]

# 连续遇到多少条 notice_date < last_date 的公告后即认为已回到旧区域
OLD_STOP = 5
# 单次常规扫描页数上限（page_size=50），防异常失控
SCAN_PAGE_LIMIT = 300
# 首次无游标/回扫时最多扫描页数（尽力覆盖列表接口可达窗口，约一个月）
INITIAL_PAGE_LIMIT = 1000
# 拉页并发数（列表接口轻量，6 路并发即可显著提速，又不会触发限流）
FEED_CONCURRENCY = 6
# 连续空页数达到该值才认为数据到底（容忍单页偶发失败）
EMPTY_STOP = 3


# ---------------- 文本/日期工具 ----------------

def norm_date(s: str) -> str:
    """归一为 YYYY-MM-DD（支持 2026年9月1日 / 2026-09-01 等）"""
    if not s:
        return ""
    s = str(s).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
        return s[:10]
    m = re.search(r"(?P<y>(?:19|20)\d{2})\s*[年.\-/]\s*(?P<mo>\d{1,2})\s*[月.\-/]\s*(?P<d>\d{1,2})\s*日?", s)
    if m:
        return f"{m.group('y')}-{int(m.group('mo')):02d}-{int(m.group('d')):02d}"
    m = re.search(r"(?P<mo>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日", s)
    if m:
        return f"{datetime.now().year}-{int(m.group('mo')):02d}-{int(m.group('d')):02d}"
    return ""


def _clean(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\u3000", " ").replace("\r", "")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。；;！!？?\n]", text)
    return [p.strip(" \t\u3000") for p in parts if len(p.strip(" \t\u3000")) >= 6]


# ---------------- 字段解析（尽力而为） ----------------

_SHARE_PATS = [
    re.compile(r"每持有(?P<n>\d[\d,]*)\s*股"),
    re.compile(r"每持(?P<n>\d[\d,]*)\s*股"),
    re.compile(r"每(?P<n>\d[\d,]*)\s*股"),
    re.compile(r"(?:持有|持股数?)\s*(?:公司|本公司)?(?:股票)?[^。；;0-9\n]{0,16}(?P<n>\d[\d,]*)\s*股"),
    re.compile(r"(?:不低于|不少于|至少|达到|满)(?P<n>\d[\d,]*)\s*股"),
]
_HAND_PAT = re.compile(r"(?P<n>\d[\d,]*)\s*手")

# 强关键词：几乎只在“真回馈内容”出现
_STRONG_REWARD = ["礼盒", "礼包", "礼品", "赠送", "免费", "代金券", "抵用券",
                  "优惠券", "提货", "酒券", "分红券", "观影券"]
# 弱关键词：需与其他条件配合，否则不进入回馈摘要
_MED_REWARD = ["优惠", "折扣", "会员", "体验", "补贴", " 元", "回馈", "箱", "瓶"]
# 明显是公告头/免责/套话，不该当回馈内容
_NOISE = ["本公司及董事会", "虚假记载", "误导性陈述", "重大遗漏", "特此公告",
          "敬请广大投资者", "董事会全体成员", "证券代码", "公告编号",
          "为感谢广大股东", "活动概况", "一、", "二、", "三、", "四、", "五、"]


def _tail(text: str, pos: int, maxlen: int = 10) -> str:
    """取 pos 之后直到标点（。；;，,）且不超过 maxlen 的一小段。"""
    rest = text[pos:]
    cut = 0
    for i, ch in enumerate(rest):
        if ch in "。；;,，":
            cut = i
            break
    else:
        cut = len(rest)
    return _clean(rest[:min(maxlen, cut)])


def parse_shares(text: str) -> str:
    """抽取股数门槛/规则，最多两条不同门槛短语；失败返回空串。"""
    text = _clean(text)
    frags = []
    qtys = set()
    for pat in _SHARE_PATS:
        for m in pat.finditer(text):
            n = m.group("n").replace(",", "")
            try:
                qty = float(n)
            except ValueError:
                continue
            if qty > 500_000_000:  # 总量级数字（注册资本等），不是门槛
                continue
            if qty in qtys:
                continue
            qtys.add(qty)
            frags.append(_clean(m.group(0) + _tail(text, m.end(), 8)).strip(" ，,、"))
            if len(frags) >= 2:
                break
        if len(frags) >= 2:
            break
    if not frags:
        m = _HAND_PAT.search(text)
        if m:
            return f"每{m.group('n')}手(100股/手)"
        return ""
    return "；".join(frags)[:120]


def _sentence_score(s: str) -> int:
    strong = sum(1 for k in _STRONG_REWARD if k in s)
    med = sum(1 for k in _MED_REWARD if k in s)
    digit = 1 if re.search(r"\d", s) else 0
    if any(k in s for k in _NOISE):
        return -1
    if re.match(r"^(?:关于|宜宾|本公司|各位|尊敬的)", s) and "公告" in s[:14]:
        return -1
    return strong * 3 + med + digit


def parse_reward(text: str) -> str:
    """抽取「回馈内容」：按关键词强度评分，挑最有信息量且不超过 3 条句子。"""
    cands = []
    for s in _sentences(text):
        sc = _sentence_score(s)
        if sc > 0:
            cands.append((sc, s))
    cands.sort(key=lambda x: (-x[0], x[1].count("。") ))
    best = [s for _, s in cands[:3]]
    if not best:
        return ""
    out = "；".join(best)
    if len(out) > 240:
        out = out[:240]
    return out


def parse_register_date(text: str) -> str:
    for m in re.finditer(r"(?:股权登记日|登记日|登记在册)", text):
        d = norm_date(text[m.end():m.end() + 40])
        if d:
            return d
    m = re.search(r"(?:截至|截止)(?P<d>\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    return norm_date(m.group("d")) if m else ""


def parse_requirement(text: str) -> str:
    """抽取「股东要求」：取含 登记日/在册/收盘/截止/限购 等条件、且提到股东的句子。"""
    conds = []
    for s in _sentences(text):
        if ("股东" in s) and any(k in s for k in
                                 ["股权登记日", "登记在册", "收市后", "收盘",
                                  "截止", "截至", "限购", "每人", "每位股东",
                                  "单个股东", "账户", "不得", "需在"]):
            conds.append(s[:150])
        if len(conds) >= 1:
            break
    if conds:
        return "；".join(dict.fromkeys(conds))[:260]
    reg = parse_register_date(text)
    if reg:
        return f"登记日：{reg}（当日收盘在册股东）"
    return ""


def parse_fields(title: str, body: str) -> dict:
    return {
        "shares": parse_shares(body),
        "reward": parse_reward(body),
        "requirement": parse_requirement(body),
    }


# ---------------- 东财公告抓取 ----------------

def _match_candidate(title: str) -> bool:
    """标题候选判断：标题同时含「股东」与任一活动词（宽召回，正文再二次确认）。"""
    if "股东" not in title:
        return False
    return any(k in title for k in _TITLE_ACT)


def _confirm_body(body: str) -> bool:
    """正文二次确认：必须有强福利词 + 资格词，排除投资活动/减持/股东会等误报。

    正文拉取失败时由调用方自行决定（此处默认不通过，避免误收）。
    """
    if not body:
        return False
    if not any(k in body for k in _BODY_STRONG):
        return False
    return any(k in body for k in _BODY_ELIG)


def _feed_item(it: dict) -> dict:
    codes = (it.get("codes") or [{}])[0] or {}
    title = it.get("title_ch") or it.get("title") or ""
    name = (codes.get("short_name") or "").strip()
    if not name and ":" in title:
        name = title.split(":", 1)[0]
    return {
        "art_code": it.get("art_code") or "",
        "code": str(codes.get("stock_code") or ""),
        "name": name,
        "title": title,
        "notice_date": (it.get("notice_date") or "")[:10],
    }


async def _fetch_page(client: httpx.AsyncClient, page: int) -> list[dict]:
    url = FEED_URL.format(page=page)
    for _ in range(4):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return (r.json().get("data") or {}).get("list") or []
        except Exception:
            pass
        await asyncio.sleep(1.2)
    return []


async def scan_feed(cursor: dict | None, page_limit: int = SCAN_PAGE_LIMIT,
                    collect_all_matches: bool = True) -> tuple[list[dict], dict]:
    """扫描全市场公告（增量），返回 (本次看到的所有命中候选, 新游标)。

    游标：{"last_date": 上次已完整扫到的公告日(YYYY-MM-DD)}。
    停止：连续 OLD_STOP 条公告的 notice_date < last_date，即回到旧区域。
    抓取：以 FEED_CONCURRENCY 并发拉页，按页序处理，兼顾速度与限流。
    """
    last_date = (cursor or {}).get("last_date") or ""
    matched: list[dict] = []
    seen: set[str] = set()
    max_date = last_date
    old_run = 0
    empty_run = 0
    page = 1

    async with httpx.AsyncClient(timeout=25, headers=HEADERS,
                                 follow_redirects=True) as client:
        while page <= page_limit:
            hi = min(page + FEED_CONCURRENCY - 1, page_limit)
            pages = await asyncio.gather(*[_fetch_page(client, p)
                                          for p in range(page, hi + 1)])
            stopped = False
            for items in pages:
                if not items:
                    # 容忍单页偶发失败：连续 EMPTY_STOP 页为空才认为数据到底
                    empty_run += 1
                    if empty_run >= EMPTY_STOP:
                        stopped = True
                        break
                    continue
                empty_run = 0
                for it in items:
                    date = (it.get("notice_date") or "")[:10]
                    art = it.get("art_code") or ""
                    title = it.get("title_ch") or it.get("title") or ""
                    if date > max_date:
                        max_date = date
                    if date and last_date and date < last_date:
                        old_run += 1
                        if old_run >= OLD_STOP:
                            stopped = True
                            break
                    else:
                        old_run = 0
                    if art and _match_candidate(title) and art not in seen:
                        seen.add(art)
                        if collect_all_matches:
                            matched.append(_feed_item(it))
                if stopped:
                    break
            page = hi + 1
            if page % 100 == 0:
                print(f"[huikui] 已扫 {page - 1} 页，当前日期 {max_date or '-'}", flush=True)
            if stopped:
                print(f"[huikui] 扫描停止于第 {page - 1} 页（回到旧区域/数据到底）", flush=True)
                break

    print(f"[huikui] 扫描结束：实际处理 {page - 1} 页，命中标题候选 {len(matched)} 条", flush=True)
    return matched, {"last_date": max_date}


# ---------------- 详情正文 ----------------

def fetch_detail(art_code: str) -> dict:
    """拉取公告详情：纯文本正文 + PDF 静态链接；失败返回空 dict。"""
    try:
        r = httpx.get(DETAIL_URL.format(art=art_code), headers=HEADERS,
                      timeout=25, follow_redirects=True)
        if r.status_code != 200:
            return {}
        r.encoding = "utf-8"
        d = (r.json().get("data") or {})
        return {
            "body": _clean(d.get("notice_content") or ""),
            "pdf": (d.get("attach_url") or "").strip(),
        }
    except Exception as e:
        print(f"[huikui] 详情拉取失败 {art_code}: {e}")
        return {}


def build_event(item: dict) -> dict | None:
    """把一条命中公告变成结构化事件记录（含正文解析结果）。

    若成功拉到正文但正文确认不是股东福利/回馈类（投资活动/股东会等误报），
    返回 None 交由调用方丢弃；正文拉取失败时保守保留（避免漏收）。
    """
    detail = fetch_detail(item["art_code"])
    body = detail.get("body") or ""
    if body and not _confirm_body(body):
        print(f"[huikui] 正文未确认是股东福利活动，跳过：{item.get('title', '')[:40]}")
        return None
    fields = parse_fields(item["title"], body) if body else {}
    return {
        "id": item["art_code"],
        "source": "auto",
        "code": item["code"],
        "name": item["name"],
        "title": item["title"],
        "notice_date": item["notice_date"],
        "shares": fields.get("shares", ""),
        "reward": fields.get("reward", ""),
        "requirement": fields.get("requirement", ""),
        "link": (f"https://data.eastmoney.com/notices/detail/"
                 f"{item['code']}/{item['art_code']}.html"),
        "pdf": detail.get("pdf", ""),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
