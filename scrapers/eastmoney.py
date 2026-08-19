"""东方财富数据抓取"""
import requests
import json
import re
import time
from datetime import datetime, date


def safe_float(v):
    """将各类值安全转为 float，失败/空返回 None。"""
    try:
        if v in (None, "", "-", "None"):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed


_CN_YEAR = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
            '七': 7, '八': 8, '九': 9, '十': 10}


def _year_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return _CN_YEAR.get(s)


def parse_coupon_ladder_from_explain(text: str) -> str | None:
    """从东财 '利率说明' 文字解析阶梯票面利率为 '0.10,0.20,0.30,1.50,1.80,2.00'（%/年）。

    兼容中文年份 '第一年0.10%'、'第四年为1.50%' 等写法；忽略 '到期赎回价格为108元'
    等无年份前缀的数字。
    """
    if not text:
        return None
    pairs = re.findall(r"第\s*([0-9一二三四五六七八九十]+)\s*年[^0-9%]*([\d.]+)\s*%", text)
    if not pairs:
        return None
    d: dict[int, float] = {}
    for yr, rate in pairs:
        y = _year_to_int(yr)
        if y is None:
            continue
        try:
            d[y] = float(rate)
        except ValueError:
            continue
    if not d:
        return None
    return ",".join(str(d[y]) for y in sorted(d))


def _sf(v):
    """safe float for scrapers.eastmoney"""
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


class EastMoneyScraper:
    """东方财富 API 数据抓取"""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://data.eastmoney.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    PUSH2_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
    DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    FUNDGZ_URL = "https://fundgz.1234567.com.cn/js"

    @classmethod
    def _get_json(cls, url: str, params: dict = None, retries: int = 3) -> Optional[dict]:
        for attempt in range(retries):
            try:
                resp = requests.get(url, params=params, headers=cls.HEADERS, timeout=15)
                resp.raise_for_status()
                text = resp.text
                if text.startswith("jQuery") or text.startswith("callback"):
                    match = re.search(r"\((\{.*\})\)", text, re.DOTALL)
                    if match:
                        return json.loads(match.group(1))
                return resp.json()
            except Exception as e:
                if attempt == retries - 1:
                    print(f"[EastMoney] GET失败 {url[:60]}: {e}")
                    return None
                time.sleep(1.5)
        return None

    # ==================== 可转债实时行情 (push2) ====================

    # ==================== 可转债全市场行情（多源融合） ====================
    # 数据源说明：
    #  - 东财 push2 板块 b:MK0354 仅覆盖 321 只（非全市场），但实时字段最全（含纯债价值/到期收益率）。
    #  - 数据中心 RPT_BOND_CB_LIST 含全市场代码全集与基础信息（评级/到期/规模/正股/转股价），实时价字段不可用。
    #  - 新浪 hq.sinajs.cn 支持批量实时价（免登录），用于补齐 b:MK0354 之外的活跃转债。
    # 三者融合 -> 全市场覆盖，与集思录对齐。

    @classmethod
    def fetch_cb_list(cls) -> list[dict]:
        """获取全市场可转债实时行情（多源融合，覆盖全市场）

        权威"在交易"集合以东方财富活跃行情板(push2 b:MK0354)为准：凡在板上能取到
        实时价的债一律纳入，不再用数据中心的退市/到期/上市日做硬过滤——否则会把
        临近赎回(DELIST_DATE 为未来某天)、上市日尚未回填的新债等"仍在交易的债"误删。
        数据中心(RPT_BOND_CB_LIST)仅用于 sina 补齐未进活跃板的债、以及补全转股价/正股等
        基础信息；评级/剩余规模/剩余年限等由 merge_cb_data 用未过滤的全量 fundamentals 补全。
        """
        cls._last_cb_mode = "fallback"
        # 1) 活跃行情板（最权威"在交易"集合，含临近赎回与新上市债）
        push2 = cls._fetch_push2_quotes()
        if not push2:
            # 行情板不可用，降级为数据中心全集 + 新浪补齐
            universe = cls._fetch_cb_universe()
            if not universe:
                return []
            sina = cls._fetch_sina_quotes(list(universe.keys()), universe)
            live = []
            for code, fund in universe.items():
                q = sina.get(code)
                if not q:
                    continue
                live.append({
                    "code": code,
                    "name": fund.get("name") or q.get("name") or "",
                    "price": q.get("price"),
                    "change_pct": q.get("change_pct"),
                    "premium_rate": q.get("premium_rate"),
                    "conversion_value": q.get("conversion_value"),
                    "pure_bond_value": q.get("pure_bond_value"),
                    "transfer_price": q.get("transfer_price") or fund.get("transfer_price"),
                    "stock_price": q.get("stock_price"),
                    "ytm_before_tax": q.get("ytm_before_tax"),
                    "remaining_years": None,
                    "stock_code": q.get("stock_code") or fund.get("stock_code") or "",
                    "stock_name": q.get("stock_name") or fund.get("stock_name") or "",
                    "redeem_trig_price": q.get("redeem_trig_price"),
                    "redeem_price": q.get("redeem_price"),
                    "maturity_date_str": "",
                    "list_date_str": "",
                    "conversion_period_start_str": "",
                    "volume": q.get("volume"),
                    "turnover_rate": q.get("turnover_rate"),
                })
            return live
        cls._last_cb_mode = "full"
        # 2) 全市场代码全集（仅用于 sina 补齐未进活跃板的债 + 基础信息 enrichment）
        universe = cls._fetch_cb_universe()
        extras = [c for c in universe if c not in push2]
        sina = cls._fetch_sina_quotes(extras, universe) if extras else {}
        # 3) 合并：以"有实时行情"的代码为准，确保不漏掉在交易的债
        live = []
        for code in list(push2.keys()) + [c for c in sina if c not in push2]:
            q = push2.get(code) or sina.get(code)
            fund = universe.get(code) or {}
            live.append({
                "code": code,
                "name": q.get("name") or fund.get("name") or "",
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "premium_rate": q.get("premium_rate"),
                "conversion_value": q.get("conversion_value"),
                "pure_bond_value": q.get("pure_bond_value"),
                "transfer_price": q.get("transfer_price") or fund.get("transfer_price"),
                "stock_price": q.get("stock_price"),
                "ytm_before_tax": q.get("ytm_before_tax"),
                "remaining_years": None,  # 由 merge 依据到期日计算
                "stock_code": q.get("stock_code") or fund.get("stock_code") or "",
                "stock_name": q.get("stock_name") or fund.get("stock_name") or "",
                "redeem_trig_price": q.get("redeem_trig_price"),
                "redeem_price": q.get("redeem_price"),
                "maturity_date_str": "",
                "list_date_str": "",
                "conversion_period_start_str": "",
                "volume": q.get("volume"),
                "turnover_rate": q.get("turnover_rate"),
            })
        return live

    @classmethod
    def _parse_date_str(cls, val):
        if not val:
            return None
        try:
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    @classmethod
    def _fund_listing_date(cls, val):
        """东财基金 f26 为上市日期（int 如 20201221 或 None），转为 'YYYY-MM-DD' 字符串。
        MK0404/MK0405 板块返回的全是 LOF/定开基金，行情 API 无封闭到期日字段，
        故以上市日作为该栏可展示的真实日期。"""
        if val is None:
            return None
        try:
            s = str(int(float(val)))
            if len(s) == 8:
                return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        except (ValueError, TypeError):
            pass
        return None

    @classmethod
    def _fetch_cb_universe(cls) -> dict:
        """数据中心取全市场可转债活跃代码全集 + 基础信息，返回 {code: {...}}。"""
        fund_index = {}
        items = []
        for attempt in range(3):
            items = []
            ok = True
            for page in range(1, 4):
                params = {
                    "reportName": "RPT_BOND_CB_LIST",
                    "columns": ("SECURITY_CODE,SECURITY_NAME_ABBR,LISTING_DATE,DELIST_DATE,"
                                "EXPIRE_DATE,RATING,ACTUAL_ISSUE_SCALE,CONVERT_STOCK_CODE,"
                                "SECURITY_SHORT_NAME,TRANSFER_START_DATE,TRANSFER_PRICE"),
                    "pageSize": "500", "pageNumber": str(page),
                    "sortColumns": "BOND_START_DATE", "sortTypes": "-1",
                    "source": "WEB", "client": "WEB",
                }
                data = cls._get_json(cls.DC_URL, params)
                if data and data.get("result") and data["result"].get("data"):
                    items.extend(data["result"]["data"])
                else:
                    ok = False
                    break
            if ok and items:
                break
            if attempt < 2:
                time.sleep(2)
        if not items:
            return fund_index
        today = date.today()
        for it in items:
            code = str(it.get("SECURITY_CODE", ""))
            if not code:
                continue
            listing = cls._parse_date_str(it.get("LISTING_DATE"))
            delist = cls._parse_date_str(it.get("DELIST_DATE"))
            expire = cls._parse_date_str(it.get("EXPIRE_DATE"))
            # 仅保留：已上市、未退市、未到期
            if not listing or delist:
                continue
            if expire and expire < today:
                continue
            fund_index[code] = {
                "name": str(it.get("SECURITY_NAME_ABBR", "")),
                "stock_code": str(it.get("CONVERT_STOCK_CODE", "")).strip(),
                "stock_name": str(it.get("SECURITY_SHORT_NAME", "")),
                "transfer_price": _sf(it.get("TRANSFER_PRICE")),
                "rating": str(it.get("RATING", "")).replace("sti", "").strip(),
            }
        return fund_index

    @classmethod
    def _fetch_push2_quotes(cls) -> dict:
        """东财板块 b:MK0354 完整行情，返回 {code: quote_dict}。"""
        out = {}
        seen = set()
        pn = 1
        while True:
            params = {
                "fid": "f3", "po": "1", "pz": "500", "pn": str(pn),
                "np": "1", "fltt": "2", "invt": "2", "fs": "b:MK0354",
                "fields": "f2,f3,f12,f14,f26,f167,f169,"
                          "f227,f229,f230,f231,f232,f234,f235,f236,f237,"
                          "f240,f241,f242,f243",
            }
            data = cls._get_json(cls.PUSH2_URL, params)
            if not data or "data" not in data:
                break
            items = data["data"].get("diff", []) or []
            if not items:
                break
            for item in items:
                code = str(item.get("f12", ""))
                if not code or code in seen:
                    continue
                seen.add(code)
                out[code] = {
                    "name": str(item.get("f14", "")),
                    "price": item.get("f2"),
                    "change_pct": item.get("f3"),
                    "premium_rate": item.get("f237"),
                    "conversion_value": item.get("f236"),
                    "pure_bond_value": item.get("f227"),
                    "transfer_price": item.get("f235"),
                    "stock_price": item.get("f229"),
                    # 税前到期收益率不参与实时行情映射：东财 push2 f230「最新到期收益率」
                    # 对折价债（价格<面值）口径有误（如三房市价 75.88 却显示 -0.63%，
                    # 标准到期收益率应为 +14%），故 ytm_before_tax 改由 data_merger 按
                    # 标准 IRR 公式（票面利率+当前价+剩余年限）重算，与券商 APP 一致。
                    "ytm_before_tax": None,
                    "stock_code": str(item.get("f232", "")),
                    "stock_name": str(item.get("f234", "")),
                    "redeem_trig_price": item.get("f240"),
                    "redeem_price": item.get("f241"),
                    "volume": item.get("f167"),
                    "turnover_rate": item.get("f169"),
                }
            if len(items) < 100:
                break
            pn += 1
            time.sleep(0.3)
        return out

    @classmethod
    def _fetch_push2_quotes_list(cls) -> list:
        """降级：仅东财板块，返回 live_bonds 列表（旧行为）。"""
        return [dict(v, code=k) for k, v in cls._fetch_push2_quotes().items()]

    @classmethod
    def _sina_symbol(cls, code: str) -> str:
        """转债/股票代码 -> 新浪 symbol（sh/sz 前缀）"""
        code = code.strip()
        if code.startswith("6") or code.startswith("11"):
            return "sh" + code
        return "sz" + code

    @classmethod
    def _fetch_sina_quotes(cls, extra_codes: list, universe: dict) -> dict:
        """新浪批量实时价补齐剩余活跃代码，并计算转股价值/转股溢价率。"""
        if not extra_codes:
            return {}
        bond_sym = {}
        stock_sym = {}
        for code in extra_codes:
            fund = universe.get(code, {})
            bond_sym[cls._sina_symbol(code)] = code
            sc = (fund.get("stock_code") or "").strip()
            if sc:
                stock_sym[cls._sina_symbol(sc)] = sc
        all_syms = list(bond_sym.keys()) + list(stock_sym.keys())
        bond_prices = {}
        stock_prices = {}
        H = {**cls.HEADERS, "Referer": "https://finance.sina.com.cn/"}
        for i in range(0, len(all_syms), 120):
            batch = all_syms[i:i + 120]
            try:
                r = requests.get("https://hq.sinajs.cn/list=" + ",".join(batch),
                                 headers=H, timeout=15)
                r.encoding = "gbk"
            except Exception:
                continue
            for line in r.text.strip().split("\n"):
                if "=\"" not in line:
                    continue
                sym = line.split("=")[0].replace("var hq_str_", "").strip()
                m = re.search(r"\"(.*)\"", line)
                if not m or not m.group(1):
                    continue
                parts = m.group(1).split(",")
                if len(parts) < 4:
                    continue
                price = _sf(parts[3])
                prev = _sf(parts[2]) if len(parts) > 2 else None
                if price is None or price <= 0:
                    continue
                if sym in bond_sym:
                    vol = parts[8] if len(parts) > 8 else None
                    bond_prices[bond_sym[sym]] = {"price": price, "prev": prev, "volume": vol}
                elif sym in stock_sym:
                    stock_prices[stock_sym[sym]] = price
        quotes = {}
        for code in extra_codes:
            bp = bond_prices.get(code)
            if not bp:
                continue
            fund = universe.get(code, {})
            sc = (fund.get("stock_code") or "").strip()
            sp = stock_prices.get(sc) if sc else None
            tp = fund.get("transfer_price")
            conv_val = None
            prem = None
            if sp and tp and tp > 0:
                conv_val = round(sp * 100 / tp, 2)
                if conv_val > 0:
                    prem = round((bp["price"] / conv_val - 1) * 100, 2)
            chg = None
            if bp.get("prev") and bp["prev"] > 0:
                chg = round((bp["price"] / bp["prev"] - 1) * 100, 2)
            quotes[code] = {
                "name": fund.get("name"),
                "price": bp["price"],
                "change_pct": chg,
                "premium_rate": prem,
                "conversion_value": conv_val,
                "pure_bond_value": None,
                "transfer_price": tp,
                "stock_price": sp,
                "ytm_before_tax": None,
                "redeem_trig_price": None,
                "redeem_price": None,
                "volume": bp.get("volume"),
                "turnover_rate": None,
                "stock_code": sc,
                "stock_name": fund.get("stock_name"),
            }
        return quotes

    # ==================== 正股行业/概念（push2 stock/get 并发查询） ====================

    @classmethod
    def _make_secid(cls, stock_code: str) -> str:
        """根据正股代码生成 secid（沪市1. 深市0. 北交所0.）"""
        code = str(stock_code).strip()
        if code.startswith("6"):
            return f"1.{code}"
        return f"0.{code}"

    @classmethod
    def fetch_stock_concepts(cls, stock_codes: list[str]) -> dict[str, dict]:
        """并发查询正股的行业(f127)和概念板块(f129)，返回 {stock_code: {industry, concept}}"""
        if not stock_codes:
            return {}

        def _fetch_one(stock_code: str):
            secid = cls._make_secid(stock_code)
            if not secid or secid.endswith("."):
                return stock_code, "", ""
            url = "https://push2delay.eastmoney.com/api/qt/stock/get"
            params = {"secid": secid, "fields": "f127,f129"}
            # 东财对 stock/get 并发敏感，失败重试以降低限流导致的空数据
            for attempt in range(3):
                try:
                    resp = requests.get(url, params=params, headers=cls.HEADERS, timeout=8)
                    d = resp.json().get("data") or {}
                    ind = str(d.get("f127", ""))
                    con = str(d.get("f129", ""))
                    if ind or con:
                        return stock_code, ind, con
                except Exception:
                    pass
                time.sleep(0.8)
            return stock_code, "", ""

        result = {}
        # 并发降到 5，避免触发东财限流（99 只并发会被整批拦截）
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_one, code): code for code in stock_codes if code}
            for future in as_completed(futures):
                stock_code, industry, concept = future.result()
                if stock_code:
                    result[stock_code] = {
                        "stock_industry": industry if industry and industry != "-" else "",
                        "concept": concept if concept and concept != "-" else "",
                    }
        return result

    @classmethod
    def fetch_stock_financials(cls, stock_codes: list[str]) -> dict[str, float]:
        """并发查询正股的净资产（所有者权益合计 TOTAL_EQUITY_PK），返回 {stock_code: net_assets}。
        用于过滤“净资产为负”的转债正股。"""
        if not stock_codes:
            return {}

        def _fetch_one(stock_code: str):
            params = {
                "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                "columns": "SECURITY_CODE,TOTAL_EQUITY_PK",
                "filter": f'(SECURITY_CODE="{stock_code}")',
                "pageSize": "1", "pageNumber": "1",
                "source": "WEB", "client": "WEB",
            }
            for attempt in range(3):
                try:
                    resp = requests.get(cls.DC_URL, params=params, headers=cls.HEADERS, timeout=8)
                    d = resp.json()
                    res = d.get("result")
                    if res and res.get("data"):
                        val = res["data"][0].get("TOTAL_EQUITY_PK")
                        if val is not None:
                            return stock_code, float(val)
                    return stock_code, None
                except Exception:
                    pass
                time.sleep(0.6)
            return stock_code, None

        result = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_one, code): code for code in stock_codes if code}
            for future in as_completed(futures):
                stock_code, val = future.result()
                if stock_code and val is not None:
                    result[stock_code] = val
        return result

    # ==================== 可转债基础信息（信用评级/强赎状态/发行规模） ====================

    @classmethod
    def fetch_cb_fundamentals(cls) -> list[dict]:
        """通过数据中心获取信用评级、赎回状态、发行规模等"""
        all_items = []
        for page in range(1, 4):  # 每页500，3页=1500条覆盖所有
            params = {
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "SECURITY_CODE,RATING,IS_REDEEM,REDEEM_TYPE,REDEEM_CLAUSE,TRANSFER_START_DATE,"
                           "EXPIRE_DATE,ACTUAL_ISSUE_SCALE,LISTING_DATE,SECURITY_SHORT_NAME",
                "pageSize": "500", "pageNumber": str(page),
                "sortColumns": "BOND_START_DATE", "sortTypes": "-1",
                "source": "WEB", "client": "WEB",
            }
            data = cls._get_json(cls.DC_URL, params)
            if data and data.get("result") and data["result"].get("data"):
                all_items.extend(data["result"]["data"])
            else:
                break
        return all_items

    @classmethod
    def fetch_cb_coupon_rates(cls) -> dict:
        """从东财 RPT_BOND_CB_LIST 的 INTEREST_RATE_EXPLAIN 字段解析阶梯票面利率。

        返回 {转债代码: '0.10,0.20,0.30,1.50,1.80,2.00'}（%/年，阶梯字符串）。
        东财数据中心可达，覆盖全市场全部转债，无需集思录 cookie —— 因此税后 YTM
        在本环境（集思录被网络限制）也能正常计算。
        """
        out: dict[str, str] = {}
        for page in range(1, 4):
            params = {
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "SECURITY_CODE,INTEREST_RATE_EXPLAIN",
                "pageSize": "500", "pageNumber": str(page),
                "sortColumns": "BOND_START_DATE", "sortTypes": "-1",
                "source": "WEB", "client": "WEB",
            }
            data = cls._get_json(cls.DC_URL, params)
            if not (data and data.get("result") and data["result"].get("data")):
                break
            for item in data["result"]["data"]:
                code = str(item.get("SECURITY_CODE", "")).strip()
                explain = item.get("INTEREST_RATE_EXPLAIN")
                if not code or not explain:
                    continue
                ladder = parse_coupon_ladder_from_explain(str(explain))
                if ladder:
                    out[code] = ladder
        return out

    # ==================== 封闭基金 ====================

    @classmethod
    def fetch_cb_remaining_sizes(cls, codes: list[str], workers: int = 8) -> dict:
        """逐债查询东财 RPTA_WEB_KZZ_LS 取最新「剩余份额」(SYFE, 单位元)，折算为剩余规模(亿元)。

        背景：RPT_BOND_CB_LIST 仅有发行规模(ACTUAL_ISSUE_SCALE)，没有「剩余规模(余额)」。
        若直接用发行规模当剩余规模，会把已大量转股、余额<5亿的债误判为>5亿而剔除
        （如盛航转债：发行7.4亿、剩余仅4.54亿）。剩余规模需用此按日序列报告的 SYFE
        字段，取最新非空值，再 ÷1e8 得到「亿元」。

        返回 {code: 剩余规模_亿元}；网络失败或该债无 SYFE 数据的 code 不出现在结果中。
        """
        if not codes:
            return {}
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(code):
            params = {
                "reportName": "RPTA_WEB_KZZ_LS",
                "columns": "DATE,SYFE,ZCODE",
                "source": "WEB", "client": "WEB",
                "filter": f'(zcode="{code}")',
                "pageNumber": "1", "pageSize": "5",
                "sortColumns": "DATE", "sortTypes": "-1",
            }
            try:
                d = cls._get_json(cls.DC_URL, params)
                rows = (d or {}).get("result") and d["result"].get("data") or []
                for r in rows:
                    syfe = r.get("SYFE")
                    if syfe not in (None, "", "-"):
                        return code, round(float(syfe) / 1e8, 4)
            except Exception:
                pass
            return code, None

        out = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one, c): c for c in codes}
            for f in as_completed(futs):
                try:
                    code, val = f.result()
                    if val is not None:
                        out[code] = val
                except Exception:
                    pass
        return out

    @classmethod
    def fetch_traded_funds(cls) -> list[dict]:
        all_funds = []
        for fs_code in ["b:MK0404", "b:MK0405"]:
            params = {
                "fid": "f3", "po": "1", "pz": "500", "pn": "1",
                "np": "1", "fltt": "2", "invt": "2", "fs": fs_code,
                "fields": "f2,f3,f12,f14,f20,f21,f26,f100,f143,f144,f145,f168,f169",
            }
            data = cls._get_json(cls.PUSH2_URL, params)
            if not data or "data" not in data:
                continue
            items = data["data"].get("diff", []) or []
            for item in items:
                # 注意：push2 字段多为字符串，必须转 float；f143=单位净值(用于折价率)，
                # f2=最新价，f145=累计净值(不参与折价计算)，f144 与 f2 同值(价格)。
                price = _sf(item.get("f2"))        # 最新价(元)
                nav = _sf(item.get("f143"))        # 单位净值(元)
                discount = None
                if nav and nav > 0 and price is not None:
                    discount = round((nav - price) / nav * 100, 2)
                all_funds.append({
                    "code": str(item.get("f12", "")),
                    "name": str(item.get("f14", "")),
                    "price": price,
                    "change_pct": _sf(item.get("f3")),
                    "nav": nav,
                    "discount_rate": discount,
                    "total_cap": _sf(item.get("f20")),
                    "circulating_cap": _sf(item.get("f21")),
                    "turnover_rate": _sf(item.get("f169")),
                    "_raw_amt": _sf(item.get("f168")),     # 成交额(元)
                    # 成交额异常为负（数据噪声）时置空，避免前端显示负数
                    "amount": (lambda a: a if (a is not None and a >= 0) else None)(_sf(item.get("f168"))),
                    "fund_type": str(item.get("f100", "")),
                    # 东财封基板块(MK0404/MK0405)返回的全是 LOF/定开基金，行情 API 无"封闭到期日"字段；
                    # f26 为真实可取的基金上市日期，复用 maturity_date 列，前端"到期时间"栏显示上市日并注明。
                    "expire_date": cls._fund_listing_date(item.get("f26")),
                })
        return all_funds

    @classmethod
    def fetch_fund_nav(cls, fund_code: str) -> Optional[dict]:
        url = f"{cls.FUNDGZ_URL}/{fund_code}.js"
        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=10)
            resp.raise_for_status()
            match = re.search(r"jsonpgz\((\{.*\})\)", resp.text)
            if match:
                return json.loads(match.group(1))
        except Exception as e:
            print(f"[EastMoney] 基金净值失败 {fund_code}: {e}")
        return None

    # ==================== 待发行可转债（抢权） ====================

    @classmethod
    def _detect_market(cls, stock_code: str) -> str:
        """根据正股代码判断市场：sh=沪市 / sz=深市 / bj=京市"""
        code = str(stock_code or "").strip()
        if code.startswith(("60", "68")):
            return "sh"
        if code.startswith(("00", "30")):
            return "sz"
        if code.startswith("8"):
            return "bj"
        return "sz"  # 默认按深市规则兜底

    @classmethod
    def _calc_min_lot(cls, per_share, market: str):
        """计算一手党所需股数与预计获配张数。

        沪市(sh)：配售单位为「手」(1手=10张=1000元)，采用「精确算法」，
                  实际为稳配1手只需买入「满配股数」的 50% 以上即可。
                  满配1手所需股数 = 1000 / 每股配售额，
                  故一手党股数 = ceil(500 / 每股配售额)，再向上取整到100股(最小交易单位)。
        深市/京市(sz/bj)：配售单位为「张」(1张=100元)，按张精确配售、可有零有整。
                  为配够1手(10张=1000元)需 股数 = ceil(1000 / 每股配售额)，向上取整到100股；
                  实际获配张数 = floor(股数 * 每股配售额 / 100)，常为奇数(有零有整)。
        返回 (股数, 预计获配张数)；数据不足返回 (None, None)。
        """
        import math
        if not per_share or per_share <= 0:
            return None, None
        if market == "sh":
            raw = 500.0 / per_share          # 沪市 50% 规则
        else:
            raw = 1000.0 / per_share         # 深市/京市 满配10张
        shares = math.ceil(raw)
        # A股最小买入单位为 100 股
        shares = ((shares + 99) // 100) * 100
        amount = shares * per_share  # 预计获配金额(元)
        if market == "sh":
            # 沪市配售单位为「手」(1手=10张=1000元)，采用精确算法：
            # 余数按手分配，>=0.5手(即500元)即保证至少配到1手。
            hands = int(amount // 1000)
            if (amount - hands * 1000) >= 500:
                hands += 1
            bonds = hands * 10
        else:
            # 深市/京市按「张」(100元)精确配售，可为奇数(有零有整)
            bonds = int(amount // 100)
        return shares, bonds

    @classmethod
    def _derive_progress(cls, item: dict, today: date) -> str:
        """根据发行公告日期推导进度（以公告为准）。

        RPT_BOND_CB_LIST 不含监管审核进度字段，这里用最可靠的「发行公告」日期推导：
          - 有申购日(PUBLIC_START_DATE)且晚于今天 → 待申购（已同意注册排期）
          - 有申购日且已过、尚未上市            → 已申购待上市
          - 有发行起始日(BOND_START_DATE)晚于今天 → 待发行
          - 其余（已出现在待发列表、有发行安排）  → 同意注册（已获批待排期）
        """
        public_date = item.get("PUBLIC_START_DATE")
        bond_start = item.get("BOND_START_DATE")
        if public_date:
            try:
                pd = datetime.strptime(str(public_date)[:10], "%Y-%m-%d").date()
                if pd > today:
                    return "待申购"
                return "已申购待上市"
            except (ValueError, IndexError):
                pass
        if bond_start:
            try:
                bd = datetime.strptime(str(bond_start)[:10], "%Y-%m-%d").date()
                if bd > today:
                    return "待发行"
            except (ValueError, IndexError):
                pass
        return "同意注册"

    @classmethod
    def fetch_new_cb_issues(cls) -> list[dict]:
        """通过 datacenter API 获取待发行可转债列表，并计算百元含权量、一手党等。

        修复点：
          1) 正股价改用 datacenter quoteColumns 的 CONVERT_STOCK_PRICE（已正确缩放，
             避免原 _fetch_stock_prices 返回 price×100 的 100 倍偏差）。
          2) 一手党股数区分沪市(50%规则)/深市(按张配售)，深市可有零有整。
          3) 进度以发行公告日期为准推导（待申购/已申购待上市/待发行/同意注册）。
        """
        import math
        all_items = []
        for page in range(1, 4):
            params = {
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_SHORT_NAME,"
                           "CONVERT_STOCK_CODE,ACTUAL_ISSUE_SCALE,PUBLIC_START_DATE,"
                           "LISTING_DATE,BOND_START_DATE,RATING,ISSUE_PRICE,"
                           "FIRST_PER_PREPLACING,RECORD_DATE_SH,EXPIRE_DATE",
                # 通过 quoteColumns 直接拿到「正确缩放」的正股价（f2 经东财换算，非×100）
                "quoteColumns": "f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE",
                "pageSize": "500", "pageNumber": str(page),
                "sortColumns": "BOND_START_DATE", "sortTypes": "-1",
                "source": "WEB", "client": "WEB",
            }
            data = cls._get_json(cls.DC_URL, params)
            if data and data.get("result") and data["result"].get("data"):
                all_items.extend(data["result"]["data"])
            else:
                break

        # 筛选未上市的转债；同时剔除退债/三板（正股代码以 4 开头，或名称含「退」）
        today = date.today()
        pending = []
        for item in all_items:
            if item.get("LISTING_DATE"):
                continue
            sc = str(item.get("CONVERT_STOCK_CODE", "")).strip()
            nm = str(item.get("SECURITY_NAME_ABBR", ""))
            if sc.startswith("4") or "退" in nm:
                continue
            pending.append(item)
        if not pending:
            return []

        # 兜底：若 quoteColumns 未返回正股价，则并发查询（注意 f2/f43 为 price×100，需÷100）
        missing = [str(i.get("CONVERT_STOCK_CODE", "")).strip()
                   for i in pending
                   if i.get("CONVERT_STOCK_CODE") and not i.get("CONVERT_STOCK_PRICE")]
        fallback_prices = cls._fetch_stock_prices(missing) if missing else {}

        issues = []
        for item in pending:
            stock_code = str(item.get("CONVERT_STOCK_CODE", "")).strip()
            # 优先用 quoteColumns 的正确正股价；缺失则用兜底价÷100
            raw_price = safe_float(item.get("CONVERT_STOCK_PRICE"))
            if raw_price and raw_price > 0:
                stock_price = raw_price
            else:
                stock_price = (fallback_prices.get(stock_code) or 0) / 100.0 or None

            per_share = safe_float(item.get("FIRST_PER_PREPLACING"))
            issue_scale = safe_float(item.get("ACTUAL_ISSUE_SCALE"))
            market = cls._detect_market(stock_code)

            # 百元含权量 = 每股配售额 / 正股价 * 100（%）
            per100_value = None
            if stock_price and per_share and stock_price > 0:
                per100_value = round(per_share / stock_price * 100, 2)

            # 一手党股数 / 预计获配张数（区分沪市/深市）
            min_lot_shares, min_lot_bonds = cls._calc_min_lot(per_share, market)

            # 一手党所需资金 = 一手党股数 * 正股价
            min_lot_capital = None
            if min_lot_shares and stock_price:
                min_lot_capital = round(min_lot_shares * stock_price, 0)

            # 进度（以公告日期为准）
            progress_name = cls._derive_progress(item, today)

            issues.append({
                "stock_code": stock_code,
                "stock_name": str(item.get("SECURITY_SHORT_NAME", "")),
                "bond_code": str(item.get("SECURITY_CODE", "")),
                "bond_name": str(item.get("SECURITY_NAME_ABBR", "")),
                "issue_size": float(issue_scale) if issue_scale else None,
                "progress_name": progress_name,
                "progress_code": "",
                "public_start_date": str(item.get("PUBLIC_START_DATE"))[:10] if item.get("PUBLIC_START_DATE") else "",
                "listing_date": "",
                "bond_start_date": str(item.get("BOND_START_DATE"))[:10] if item.get("BOND_START_DATE") else "",
                "rating": str(item.get("RATING", "")).replace("sti", "").strip(),
                "per100_value": per100_value,
                "per_share": float(per_share) if per_share else None,
                "market": market,
                "min_lot_shares": min_lot_shares,
                "min_lot_bonds": min_lot_bonds,
                "min_lot_capital": min_lot_capital,
                "stock_price": stock_price,
            })
        return issues

    @classmethod
    def _fetch_stock_prices(cls, stock_codes: list[str]) -> dict:
        """并发查询多只正股的当前价格"""
        if not stock_codes:
            return {}

        def _fetch_one(stock_code: str):
            secid = cls._make_secid(stock_code)
            if not secid or secid.endswith("."):
                return stock_code, None
            url = "https://push2delay.eastmoney.com/api/qt/stock/get"
            params = {"secid": secid, "fields": "f12,f2,f43"}
            try:
                resp = requests.get(url, params=params, headers=cls.HEADERS, timeout=8)
                d = resp.json().get("data") or {}
                # 东财 push2 f2/f43 返回的是 价格×100（单位：分），须÷100 还原为元
                raw = d.get("f2") or d.get("f43")
                if raw is None:
                    return stock_code, None
                try:
                    return stock_code, float(raw) / 100.0
                except (ValueError, TypeError):
                    return stock_code, None
            except Exception:
                return stock_code, None

        result = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_fetch_one, code): code for code in stock_codes if code}
            for future in as_completed(futures):
                stock_code, price = future.result()
                if stock_code and price is not None:
                    try:
                        result[stock_code] = float(price)
                    except (ValueError, TypeError):
                        pass
        return result

    @classmethod
    def _fetch_from_kzz_page(cls) -> list[dict]:
        """抓取 data.eastmoney.com/kzz 页面的待发转债数据"""
        url = "https://data.eastmoney.com/kzz/default.html"
        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=15)
            resp.raise_for_status()
            html = resp.text

            # 页面内嵌了 JSON 数据，查找包含待发转债信息的变量
            patterns = [
                r'window\.__NUXT__\s*=\s*(\{.*?\});\s*</script>',
                r'var\s+cfg\s*=\s*(\{.*?\});',
                r'<script[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>',
            ]

            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    try:
                        cfg = json.loads(match.group(1))
                        # NUXT 结构可能嵌套较深，递归查找包含待发数据
                        issues = cls._extract_issues_from_obj(cfg)
                        if issues:
                            return issues
                    except json.JSONDecodeError:
                        continue

            # 如果 JSON 提取失败，回退到 HTML 解析
            return cls._parse_issues_html(html)

        except Exception as e:
            print(f"[EastMoney] 抓取待发列表失败: {e}")
        return []

    @classmethod
    def _extract_issues_from_obj(cls, obj, depth=0) -> list[dict]:
        """递归查找包含债券发行进度的数据"""
        if depth > 6:
            return []
        if isinstance(obj, list):
            # 检查是否像待发列表
            if obj and isinstance(obj[0], dict):
                sample = obj[0]
                if any(k in sample for k in ("bond_nm", "BOND_NAME", "securityName")):
                    return cls._normalize_issues(obj)
            for item in obj:
                result = cls._extract_issues_from_obj(item, depth + 1)
                if result:
                    return result
        elif isinstance(obj, dict):
            for key, val in obj.items():
                if isinstance(val, (list, dict)):
                    result = cls._extract_issues_from_obj(val, depth + 1)
                    if result:
                        return result
        return []

    @classmethod
    def _normalize_issues(cls, raw_list: list) -> list[dict]:
        issues = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            # 兼容多种字段命名
            bond_nm = item.get("bond_nm") or item.get("BOND_NAME") or item.get("bondName") or ""
            stock_nm = item.get("stock_nm") or item.get("STOCK_NAME") or item.get("stockName") or ""
            if not bond_nm and not stock_nm:
                continue
            issues.append({
                "stock_code": str(item.get("stock_id") or item.get("STOCK_CODE") or item.get("stockCode", "")),
                "stock_name": str(stock_nm),
                "bond_code": str(item.get("bond_id") or item.get("BOND_CODE") or item.get("bondCode", "")),
                "bond_name": str(bond_nm),
                "issue_size": item.get("issue_size") or item.get("ISSUE_SIZE") or item.get("planTotal"),
                "progress_code": str(item.get("progress") or item.get("PROGRESS_CODE") or ""),
                "progress_name": str(item.get("progress_nm") or item.get("PROGRESS_NAME") or ""),
                "board_plan_date": item.get("board_plan_date") or item.get("BOARD_PLAN_DATE"),
                "shm_approve_date": item.get("shm_approve_date") or item.get("SHM_APPROVE_DATE"),
                "listing_committee_date": item.get("listing_committee_date") or item.get("LISTING_COMMITTEE_DATE"),
                "registration_approve_date": item.get("registration_approve_date") or item.get("REGISTRATION_APPROVE_DATE"),
                "public_start_date": item.get("public_start_date") or item.get("PUBLIC_START_DATE"),
                "list_date": item.get("list_date") or item.get("LIST_DATE"),
            })
        return issues

    @classmethod
    def _parse_issues_html(cls, html: str) -> list[dict]:
        """HTML 表格解析回退"""
        issues = []
        table_match = re.search(r'<table[^>]*id="kzz_table"[^>]*>(.*?)</table>', html, re.DOTALL)
        if not table_match:
            # 尝试找其他表格
            table_match = re.search(r'<table[^>]*class="[^"]*table[^"]*"[^>]*>(.*?)</table>', html, re.DOTALL)
        if not table_match:
            return issues
        table_html = table_match.group(1)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        for row in rows:
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            if len(cells) < 4:
                continue
            issues.append({
                "stock_code": cells[0] if len(cells) > 0 else "",
                "stock_name": cells[1] if len(cells) > 1 else "",
                "bond_name": cells[2] if len(cells) > 2 else "",
                "issue_size": cells[3] if len(cells) > 3 else "",
                "progress_name": cells[4] if len(cells) > 4 else "",
            })
        return issues
