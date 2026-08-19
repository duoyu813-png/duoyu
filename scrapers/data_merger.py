"""数据合并：将实时行情与基础信息融合，并提供数据清洗工具"""
from datetime import datetime, date
from typing import Optional, Union

from scrapers.eastmoney import EastMoneyScraper
from scrapers.jisilu import JisiluScraper
from scrapers.ytm import compute_ytm_before_tax, parse_redeem_price, FACE_VALUE


def safe_float(val, default=None):
    """安全转换浮点数，'-' 和 None 返回 default"""
    if val is None or val == "-" or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val, default=""):
    """安全转换字符串"""
    if val is None or val == "-":
        return default
    return str(val)


def _parse_date(val: Optional[str]) -> Optional[date]:
    """解析日期字符串 yyyyMMdd 或 yyyy-MM-dd"""
    if not val or val in ("-", "None", ""):
        return None
    val = str(val).strip()
    try:
        return datetime.strptime(val[:10].replace("-", "")[:8], "%Y%m%d").date()
    except (ValueError, IndexError):
        return None


def merge_cb_data(live_bonds: list[dict], fundamentals: list[dict],
                  stock_concepts: dict = None,
                  stock_financials: dict = None,
                  remaining_sizes: dict = None,
                  coupon_rates: dict = None) -> list[dict]:
    """将实时行情与数据中心基础信息、正股概念、正股财务合并

    - 权威到期日取自 datacenter EXPIRE_DATE（push2 f242 实为转股起始日，不可用）
    - 剩余年限由权威到期日精确计算（保留2位小数）
    - 正股ST由正股名前缀判定
    - 净资产取自正股 F10 所有者权益合计
    """
    fund_index = {}
    for item in fundamentals:
        code = str(item.get("SECURITY_CODE", ""))
        if code:
            fund_index[code] = item

    # 剩余规模(余额)数据源：东财 RPTA_WEB_KZZ_LS 的 SYFE 字段（逐债按日剩余份额）。
    # 注意：该字段只对「已发生转股」的债有值；未/少转股的债 SYFE 为 None，
    # 此时退回发行规模(ACTUAL_ISSUE_SCALE)——对少转股债 剩余≈发行，等价正确。
    # 严禁直接用发行规模顶替剩余规模，否则会把「发行>5亿但剩余<5亿」的债误剔除
    # （如盛航转债：发行7.4亿、剩余仅4.54亿）。
    if remaining_sizes is None:
        try:
            remaining_sizes = EastMoneyScraper.fetch_cb_remaining_sizes(
                [b["code"] for b in live_bonds if b.get("code")], workers=8
            )
        except Exception:
            remaining_sizes = {}
    if remaining_sizes is None:
        remaining_sizes = {}

    # 票面利率（阶梯）：优先用东财 RPT_BOND_CB_LIST 的 INTEREST_RATE_EXPLAIN（全市场可达，
    # 无需 cookie）；集思录 cb_list_new 作备用（游客态仅前30只，配 JISILU_COOKIE 解锁全部）。
    if coupon_rates is None:
        try:
            coupon_rates = EastMoneyScraper.fetch_cb_coupon_rates()
        except Exception:
            coupon_rates = {}
        if not coupon_rates:
            try:
                coupon_rates = JisiluScraper.fetch_cb_coupon_rates()
            except Exception:
                coupon_rates = {}
    if coupon_rates is None:
        coupon_rates = {}
    today = date.today()
    for bond in live_bonds:
        code = bond["code"]
        info = fund_index.get(code, {})
        if info:
            bond["credit_rating"] = str(info.get("RATING", "")).replace("sti", "").strip()
            # 强赎判定：东财 RPT_BOND_CB_LIST 的 REDEEM_TYPE 非空（如 '2'）代表该债处于
            # "赎回相关状态"。但实测发现它同时包含两类，必须结合溢价率区分：
            #   1) 真强赎（已公告/触发强赎）：正股飙升，转债被套利至平价，溢价率≈0（约 -25%~+12%）；
            #   2) 临近到期赎回的正常债：剩余期限<1年，溢价率仍高（常 >20%）——这类本就会被
            #      "剩余年限<1年"通用过滤排除，不应算作强赎误杀。
            # 因此只有 REDEEM_TYPE 非空 且 溢价率处于平价区间（或行情缺失无法轮动）才认定强赎，
            # 确保轮动策略精准剔除正在被强赎的标的，而不误伤临近到期的正常债。
            rt = info.get("REDEEM_TYPE")
            is_redeem_flag = bool(rt not in (None, "", "0", "None", "-"))
            prem = bond.get("premium_rate")
            near_parity = (prem is None) or (-25 <= (prem or 0) <= 12)
            is_redeem = is_redeem_flag and near_parity
            bond["announced_redemption"] = is_redeem
            bond["redemption_status"] = "redeem" if is_redeem else "none"

            # 权威到期日 = datacenter EXPIRE_DATE（push2 f242 是转股起始日，不可用）
            expire = _parse_date(str(info.get("EXPIRE_DATE", "")))
            if expire:
                bond["maturity_date_str"] = expire.strftime("%Y-%m-%d")
                # 剩余年限：由权威到期日精确计算
                bond["remaining_years"] = round((expire - today).days / 365.0, 2)
            # 权威转股起始日 = datacenter TRANSFER_START_DATE
            conv_start = _parse_date(str(info.get("TRANSFER_START_DATE", "")))
            if conv_start:
                bond["conversion_period_start_str"] = conv_start.strftime("%Y-%m-%d")
            # 剩余规模(余额, 亿元)：优先用 SYFE(真实剩余份额)，无则退回发行规模
            real_size = remaining_sizes.get(code)
            issue_scale = info.get("ACTUAL_ISSUE_SCALE")
            if real_size is not None:
                bond["remaining_size"] = real_size
            elif issue_scale is not None:
                # 回退：未/少转股债 剩余≈发行，等价正确
                try:
                    bond["remaining_size"] = float(issue_scale)
                except (ValueError, TypeError):
                    bond["remaining_size"] = None
            else:
                bond["remaining_size"] = None

            # 税前到期收益率(ytm_before_tax)：按用户给定标准 IRR 公式计算。
            #   公式：价格(全价) = Σ 各期利息/(1+r)^t_i + 到期赎回价/(1+r)^t_n
            #   到期赎回价 = 面值 100 + 补偿金（取自发行公告「到期赎回条款」，含最后一年利息），
            #   必须计入——否则如美锦转债（价格 107.7、赎回价 118）会被错算为负、实为 +5%~6%。
            #   由「阶梯票面利率 + 当前行情净价 + 剩余年限 + 到期赎回价」自洽求解 r（小数→百分数）。
            #   东财 push2 的 f230 为不含补偿金口径、f235 字段异常，均不可用；本公式与券商 APP 一致。
            coupon_rate = coupon_rates.get(code)
            bond["coupon_rate"] = coupon_rate
            # 到期赎回价（含补偿金）：从东财 REDEEM_CLAUSE 解析；解析失败退回面值 100
            redeem_clause = info.get("REDEEM_CLAUSE")
            redeem_price = parse_redeem_price(redeem_clause) if redeem_clause else None
            price = bond.get("price")
            ry = bond.get("remaining_years")
            if coupon_rate is not None and price is not None and ry is not None:
                ybt = compute_ytm_before_tax(
                    coupon_rate, price, ry,
                    redeem_price=redeem_price if redeem_price is not None else FACE_VALUE,
                )
                bond["ytm_before_tax"] = round(ybt * 100.0, 4) if ybt is not None else None
            else:
                bond["ytm_before_tax"] = None

            if not bond.get("list_date_str") or bond["list_date_str"] in ("-", "None", ""):
                bond["list_date_str"] = str(info.get("LISTING_DATE", ""))
            # 正股ST：由正股名前缀判定（*ST / ST）
            sname = str(info.get("SECURITY_SHORT_NAME", ""))
            bond["stock_st"] = sname.startswith("*ST") or sname.startswith("ST")
        else:
            bond["credit_rating"] = ""
            bond["announced_redemption"] = False
            bond["stock_st"] = False

        # 合并正股行业和概念
        if stock_concepts:
            stock_code = bond.get("stock_code", "")
            concept_info = stock_concepts.get(stock_code, {})
            bond["stock_industry"] = concept_info.get("stock_industry", "")
            bond["concept"] = concept_info.get("concept", "")
        else:
            bond["stock_industry"] = ""
            bond["concept"] = ""

        # 净资产（所有者权益合计）：由正股 F10 注入
        if stock_financials:
            bond["net_assets"] = stock_financials.get(bond.get("stock_code"))
        else:
            bond["net_assets"] = None

        # 强赎满足天数：免费源无此字段，待接入公告源
        bond["redemption_days"] = 0

    return live_bonds
