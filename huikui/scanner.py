"""股东回馈活动 · 公告扫描与字段解析

数据源：
  1) 东方财富全市场公告列表（东财 np-anotice-stock 接口）—— 结构化正文，主源。
  2) 巨潮资讯全文检索（cninfo fulltextSearch）—— 覆盖更全、可回补历史，
     但只给标题 + PDF，正文由 pypdf 尽力解析。
  3) 微信公众号文章检索（搜狗微信 weixin.sogou.com）—— 尽力而为的兜底源，
     专门找“只在公众号/官网发布、未走正式公告”的活动；反爬强、可能随时失效，
     失败时静默跳过，不影响主流程。

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
import hashlib
import html
import io
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

# 巨潮/搜狗返回的是 epoch（秒/毫秒），须按北京时间换算日期，
# 否则在 UTC 的 Actions 环境会整体早一天（公告时间多为北京时间零点）。
_CST = timezone(timedelta(hours=8))


def _epoch_to_cn_date(sec: float) -> str:
    try:
        return datetime.fromtimestamp(sec, tz=_CST).strftime("%Y-%m-%d")
    except Exception:
        return ""

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
        "source": "eastmoney",
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


# ---------------- 巨潮资讯（cninfo）全文检索源 ----------------
# 说明：fulltextSearch 接口的 seDate/pageNum 实测有效，但 seDate 不过滤（始终返回
# 全量相关结果，按相关度排序），因此这里「按关键词翻完所有页 + 本地去重」，
# 首次即可回补到历史（远早于东财列表约 1 个月的窗口）。
# 检索结果只有标题 + PDF，没有纯文本正文，故正文用 pypdf 解析 PDF（尽力而为）。

CNINFO_URL = "http://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_DETAIL = ("http://www.cninfo.com.cn/new/disclosure/detail"
                 "?stockCode={code}&announcementId={aid}"
                 "&orgId={org}&announcementTime={date}")
CNINFO_PDF = "http://static.cninfo.com.cn/{path}"

CNINFO_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

# 全文检索关键词：接口在「标题 + 正文」上做匹配，可捞到标题不含「股东」的活动
CNINFO_KEYWORDS = ["股东回馈", "回馈股东", "股东福利", "股东专享",
                   "股东感恩回馈", "股东答谢", "股东礼遇", "持股有礼"]
CNINFO_PAGE_SIZE = 30
# 单关键词最多翻页数（防异常失控；实测全量约 5 页）
CNINFO_PAGE_LIMIT = 20
# PDF 正文本地解析页数上限（活动公告通常 1~3 页）
PDF_MAX_PAGES = 15


def _strip_tags(s: str) -> str:
    """去掉 cninfo 标题里的 <em> 高亮标签与 HTML 实体。"""
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()


def _cninfo_item(it: dict) -> dict | None:
    aid = str(it.get("announcementId") or "")
    title = _strip_tags(it.get("announcementTitle") or "")
    if not aid or not title:
        return None
    ts = it.get("announcementTime")
    try:
        date = _epoch_to_cn_date(int(ts) / 1000)
    except Exception:
        date = ""
    path = (it.get("adjunctUrl") or "").strip()
    code = str(it.get("secCode") or "")
    org = str(it.get("orgId") or "")
    return {
        "announcementId": aid,
        "code": code,
        "name": _strip_tags(it.get("secName") or ""),
        "title": title,
        "notice_date": date,
        "pdf": CNINFO_PDF.format(path=path) if path else "",
        "link": CNINFO_DETAIL.format(code=code, aid=aid, org=org, date=date),
    }


async def _cninfo_search(client: httpx.AsyncClient, keyword: str,
                         page: int) -> tuple[list[dict], int]:
    data = {
        "pageNum": str(page), "pageSize": str(CNINFO_PAGE_SIZE),
        "column": "szse", "tabName": "fulltext", "plate": "", "stock": "",
        "searchkey": keyword, "secid": "", "category": "", "trade": "",
        "seDate": "", "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    for _ in range(4):
        try:
            r = await client.post(CNINFO_URL, data=data)
            if r.status_code == 200:
                r.encoding = "utf-8"
                j = r.json()
                return (j.get("announcements") or [],
                        int(j.get("totalRecordNum") or 0))
        except Exception:
            pass
        await asyncio.sleep(1.2)
    return [], 0


async def scan_cninfo(keywords: list[str] | None = None,
                      page_limit: int = CNINFO_PAGE_LIMIT) -> list[dict]:
    """按关键词全文检索巨潮资讯，返回去重后的候选（含 PDF 链接，尚未解析正文）。"""
    keywords = keywords or CNINFO_KEYWORDS
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=30, headers=CNINFO_HEADERS,
                                 follow_redirects=True) as client:
        for kw in keywords:
            page = 1
            total = 0
            while page <= page_limit:
                items, tot = await _cninfo_search(client, kw, page)
                if not items:
                    break
                total = tot or total
                for it in items:
                    norm = _cninfo_item(it)
                    if norm:
                        out.setdefault(norm["announcementId"], norm)
                if total and page * CNINFO_PAGE_SIZE >= total:
                    break
                page += 1
                await asyncio.sleep(0.3)
    print(f"[huikui] 巨潮全文检索：{len(out)} 条候选（{len(keywords)} 个关键词）",
          flush=True)
    return list(out.values())


def fetch_pdf_text(url: str, max_pages: int = PDF_MAX_PAGES) -> str:
    """下载巨潮 PDF 并抽取纯文本（pypdf 缺失/解析失败时返回空串）。"""
    if not url:
        return ""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        r = httpx.get(url, headers=HEADERS, timeout=45, follow_redirects=True)
        if r.status_code != 200:
            return ""
        reader = PdfReader(io.BytesIO(r.content))
        chunks = []
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                break
            chunks.append(page.extract_text() or "")
        return _clean("\n".join(chunks))
    except Exception as e:
        print(f"[huikui] PDF 解析失败 {url}: {e}")
        return ""


def build_cninfo_event(item: dict) -> dict | None:
    """巨潮候选 -> 结构化事件；正文由 PDF 解析，解析失败时退回标题判断。"""
    body = fetch_pdf_text(item.get("pdf") or "")
    if body:
        if not _confirm_body(body):
            print(f"[huikui] 巨潮正文未确认是股东福利活动，跳过："
                  f"{item.get('title', '')[:40]}")
            return None
    elif not _match_candidate(item.get("title") or ""):
        return None
    fields = parse_fields(item["title"], body) if body else {}
    return {
        "id": "cninfo:" + item["announcementId"],
        "source": "cninfo",
        "code": item["code"],
        "name": item["name"],
        "title": item["title"],
        "notice_date": item["notice_date"],
        "shares": fields.get("shares", ""),
        "reward": fields.get("reward", ""),
        "requirement": fields.get("requirement", ""),
        "link": item["link"],
        "pdf": item.get("pdf", ""),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_cninfo_events(items: list[dict], workers: int = 6) -> list[dict]:
    """并发解析多条巨潮候选（主要是 PDF 下载+抽取耗时）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: list[dict] = []
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(build_cninfo_event, it) for it in items]
        for f in as_completed(futs):
            try:
                ev = f.result()
            except Exception as e:
                print(f"[huikui] 巨潮解析失败: {e}")
                continue
            if ev:
                out.append(ev)
    out.sort(key=lambda e: e.get("notice_date") or "", reverse=True)
    return out


# ---------------- 微信公众号（搜狗微信）兜底源 ----------------
# 说明：微信没有公开搜索 API，搜狗微信（weixin.sogou.com）是唯一半开放的入口，
# 反爬强、随时可能返回验证码。此处按关键词检索并解析标题/摘要/账号/时间，
# 定位为「尽力而为」：拿到即入库，拿不到就静默跳过，绝不影响主流程。

SOGOU_URL = "https://weixin.sogou.com/weixin"
SOGOU_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://weixin.sogou.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
WECHAT_KEYWORDS = ["股东回馈活动", "股东专享福利", "股东感恩回馈", "回馈股东"]
WECHAT_PAGES = 2


def _sogou_fetch(keyword: str, page: int) -> str:
    params = {"type": "2", "query": keyword, "ie": "utf8", "page": str(page)}
    try:
        r = httpx.get(SOGOU_URL, params=params, headers=SOGOU_HEADERS,
                      timeout=30, follow_redirects=True)
        if r.status_code != 200:
            return ""
        r.encoding = "utf-8"
        return r.text
    except Exception as e:
        print(f"[huikui] 搜狗微信请求失败「{keyword}」: {e}")
        return ""


def _parse_sogou(html_text: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r'<li id="sogou_vr_11002601_box_\d+".*?</li>',
                         html_text, re.S):
        b = m.group(0)
        hm = re.search(r'<h3>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not hm:
            continue
        link = html.unescape(hm.group(1))
        if link.startswith("/"):
            link = "https://weixin.sogou.com" + link
        title = _strip_tags(hm.group(2))
        am = (re.search(r'<span class="all-time-y2">(.*?)</span>', b, re.S)
              or re.search(r'<a[^>]*class="account"[^>]*>(.*?)</a>', b, re.S))
        account = _strip_tags(am.group(1)) if am else ""
        sm = re.search(r'class="txt-info"[^>]*>(.*?)</p>', b, re.S)
        snippet = _strip_tags(sm.group(1)) if sm else ""
        dm = re.search(r"timeConvert\('(\d+)'\)", b)
        date = ""
        if dm:
            try:
                date = _epoch_to_cn_date(int(dm.group(1)))
            except Exception:
                pass
        if not title:
            continue
        out.append({"title": title, "account": account, "snippet": snippet,
                    "notice_date": date, "link": link})
    return out


def scan_wechat(keywords: list[str] | None = None,
                pages: int = WECHAT_PAGES) -> list[dict]:
    """搜狗微信关键词检索，返回去重后的文章候选（反爬失效时返回空列表）。"""
    keywords = keywords or WECHAT_KEYWORDS
    seen: set[tuple] = set()
    out: list[dict] = []
    for kw in keywords:
        for page in range(1, pages + 1):
            txt = _sogou_fetch(kw, page)
            if not txt or "antispider" in txt or "请输入验证码" in txt \
                    or "用户您好，您的访问过于频繁" in txt:
                break
            items = _parse_sogou(txt)
            if not items:
                break
            for it in items:
                key = (it["title"], it["account"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(it)
            time.sleep(1.5)
        time.sleep(1.0)
    print(f"[huikui] 搜狗微信检索：{len(out)} 条候选", flush=True)
    return out


_NAME_NOISE = {"股票代码", "证券代码", "代码", "简称", "证券简称",
               "公司简介", "公告", "披露", "关于", "股东回馈"}
_NOISE_SUFFIX = ("活动", "公告", "代码", "简称")


def _wechat_code_name(title: str, snippet: str, account: str) -> tuple[str, str]:
    """尽力从标题/摘要里抽出股票代码与公司名，失败退回公众号名。"""
    text = f"{title} {snippet}"
    m = re.search(r"(?<!\d)(\d{6})(?:\.(?:SH|SZ|BJ|sh|sz|bj))?(?!\d)", text)
    code = m.group(1) if m else ""
    name = ""
    if code:
        pm = re.search(r"([\u4e00-\u9fa5A-Za-z]{2,10})\s*[（(]?\s*"
                       r"(?:[Ss][Hh]|[Ss][Zz]|[Bb][Jj])?[：:]?\s*" + code, text)
        if pm:
            cand = pm.group(1)
            if cand not in _NAME_NOISE and not cand.endswith(_NOISE_SUFFIX):
                name = cand
    if not name:
        tm = re.match(r"([\u4e00-\u9fa5A-Za-z0-9]{2,6})[：:]\s*\S", title)
        if tm and tm.group(1) not in _NAME_NOISE \
                and not tm.group(1).endswith(_NOISE_SUFFIX):
            name = tm.group(1)
    if not name:
        name = account
    return code, name


def build_wechat_event(item: dict) -> dict | None:
    """公众号文章候选 -> 结构化事件；摘要作为「回馈内容」的兜底。"""
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    body = f"{title} {snippet}"
    if "股东" not in body or not any(k in body for k in _TITLE_ACT):
        return None
    date = item.get("notice_date") or datetime.now().strftime("%Y-%m-%d")
    code, name = _wechat_code_name(title, snippet, item.get("account") or "")
    fields = parse_fields(title, snippet)
    reward = fields.get("reward") or _clean(snippet)[:240]
    h = hashlib.md5((title + "|" + (item.get("account") or "")).encode("utf-8"))
    return {
        "id": "wx:" + h.hexdigest()[:16],
        "source": "wechat",
        "code": code,
        "name": name,
        "title": title,
        "notice_date": date,
        "shares": fields.get("shares", ""),
        "reward": reward,
        "requirement": fields.get("requirement", ""),
        "link": item.get("link", ""),
        "pdf": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
