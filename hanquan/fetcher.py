import httpx
import re
import math
import asyncio
import os
import sys
from datetime import datetime
from typing import Optional, Any

# 支持从 hanquan 子包导入 scorer（独立运行 / 打包时路径不同）
try:
    from .scorer import score_bond
except ImportError:
    score_bond

JISILU_PRE_LIST = "https://www.jisilu.cn/data/cbnew/pre_list/"

_JISILU_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.jisilu.cn/",
}

_CONCURRENCY_LIMIT = 10
_semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)


async def fetch_pending_cbonds() -> list[dict]:
    bonds = []
    try:
        async with httpx.AsyncClient(timeout=20, headers=_JISILU_HEADERS) as cli:
            r = await cli.get(JISILU_PRE_LIST)
            if r.status_code != 200:
                return bonds
            data = r.json()
            rows = data.get("rows", [])
            for row in rows:
                cell = row.get("cell", {})
                item = _parse_jisilu_item(cell)
                if item["stock_code"]:
                    bonds.append(item)
    except Exception as e:
        print(f"[fetcher] jisilu error: {e}")
    return bonds


def _parse_jisilu_item(cell: dict) -> dict:
    sec_code = str(cell.get("stock_id", "") or "").strip()
    sec_name = str(cell.get("stock_nm", "") or "").strip()
    bond_name = str(cell.get("bond_nm", "") or "").strip()

    ration = _float(cell.get("ration")) or 0.0
    b_shares = _float(cell.get("b_shares")) or 0.0

    total_scale = _float(cell.get("amount")) or 0.0
    price = _float(cell.get("price")) or 0.0
    change_pct = _float(cell.get("increase_rt")) or 0.0
    pb = _float(cell.get("pb")) or 0.0
    convert_price = _float(cell.get("convert_price")) or 0.0
    ma20_price = _float(cell.get("ma20_price")) or 0.0
    cb_amount = _float(cell.get("cb_amount")) or 0.0

    reg_date = str(cell.get("accept_date", "") or "")[:10]
    progress_full = str(cell.get("progress_full", "") or "")
    progress_nm = str(cell.get("progress_nm", "") or "")
    progress_dt = str(cell.get("progress_dt", "") or "")

    progress = _parse_progress(progress_full, progress_nm, progress_dt)

    board = "主板"
    if sec_code.startswith("688"):
        board = "科创板"
    elif sec_code.startswith("300"):
        board = "创业板"
    elif sec_code.startswith("8"):
        board = "北交所"

    return {
        "stock_code": sec_code,
        "stock_name": sec_name,
        "bond_name": bond_name or f"{sec_name}转债",
        "industry": _guess_industry(sec_name),
        "total_scale": total_scale,
        "per_share_amount": ration,
        "progress": progress,
        "registration_date": reg_date,
        "approval_date": "",
        "board": board,
        "rating_cd": str(cell.get("rating_cd", "") or ""),
        "record_dt": str(cell.get("record_dt", "") or "")[:10],
        "convert_price": convert_price,
        "cb_amount": cb_amount,
        "b_shares": b_shares,
        "price": round(price, 2),
        "change_pct": round(change_pct, 2) if change_pct else 0.0,
        "pb": round(pb, 2) if pb else None,
        "ma20_price": round(ma20_price, 2) if ma20_price else 0.0,
    }


PROGRESS_ORDER = {
    "发行中": 0,
    "同意注册": 1,
    "上市委通过": 2,
    "交易所受理": 3,
    "股东大会审议": 4,
    "股东大会通过": 4,
    "董事会预案": 5,
    "已上市": 6,
    "其他": 7,
}

def _parse_progress(progress_full: str, progress_nm: str, progress_dt: str) -> str:
    if progress_nm:
        if "申购" in progress_nm or "配售" in progress_nm:
            return "发行中"
        if "上市" in progress_nm and "上市委" not in progress_nm:
            return "已上市"
        if "同意注册" in progress_nm:
            return "同意注册"
        if "上市委通过" in progress_nm:
            return "上市委通过"
        if "通过" in progress_nm:
            return "交易所通过"
        if "受理" in progress_nm:
            return "交易所受理"
        if "股东" in progress_nm:
            return "股东大会审议"
        if "董事" in progress_nm or "预案" in progress_nm:
            return "董事会预案"
    
    if "同意注册" in progress_full:
        return "同意注册"
    if "上市委通过" in progress_full:
        return "上市委通过"
    if "注册生效" in progress_full:
        return "同意注册"
    if "证监会" in progress_full and ("批" in progress_full or "核准" in progress_full):
        return "证监会批准"
    if "交易所" in progress_full and "通过" in progress_full:
        return "交易所通过"
    if "股东大会" in progress_full:
        return "股东大会审议"
    if "董事会" in progress_full or "预案" in progress_full:
        return "董事会预案"
    if "受理" in progress_full:
        return "交易所受理"
    
    return progress_nm or "其他"


def _float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _guess_industry(name: str) -> str:
    kw_map = [
        (r"科技|智能|AI|芯片|半导体|电子|信息|软件|数据|通信|互联|微电子|光电|元器件", "AI/科技"),
        (r"医|药|生物|健康|医疗|基因|康", "医药"),
        (r"新能源|光伏|锂|电池|充电|风电|储能|氢|能源", "新能源"),
        (r"汽|车|配件|轮胎|零部件|自动", "汽车"),
        (r"地|房产|置业|建设|建材|基建|工程|建筑", "地产/基建"),
        (r"食品|饮料|酒|乳|农业|牧|渔|粮|糖", "消费"),
        (r"化|工|材料|塑|橡胶|纤维|树脂|钛白粉|涂料", "化工/材料"),
        (r"机械|装备|设备|制造|精密|模具|刀具|机床", "机械设备"),
        (r"电力|电网|能源|电气|电工|电缆|配", "电力设备"),
        (r"环保|水务|水|环境|节能|资源", "环保"),
        (r"黄金|有色|金属|钢|铁|矿|铝|铜|锌|镍", "有色/钢铁"),
        (r"纺织|服装|鞋|帽|家纺", "纺织服装"),
        (r"航|空|天|军|兵|器|弹", "军工"),
        (r"传媒|影视|游戏|广告|出版|娱乐|旅游|酒店|餐饮", "传媒/消费"),
        (r"金融|银行|保险|证券|基金|信托", "金融"),
        (r"交|通|运输|物流|港口|机场|高速|航运|铁路|公路", "交通运输"),
    ]
    for pattern, industry in kw_map:
        if re.search(pattern, name):
            return industry
    return "其他"


def _get_market(stock_code: str) -> str:
    if stock_code.startswith('6'):
        return 'sh'
    elif stock_code.startswith('0') or stock_code.startswith('3'):
        return 'sz'
    return 'sh'


async def _fetch_stock_profile(stock_code: str) -> dict:
    market = _get_market(stock_code)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    async with _semaphore:
        try:
            url = f"http://qt.gtimg.cn/q={market}{stock_code}"
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.get(url, headers=headers)
                data = r.text
            if data:
                parts = data.split('~')
                if len(parts) >= 74:
                    total_shares_raw = _float(parts[73])
                    float_shares_raw = _float(parts[72])
                    total_shares = total_shares_raw / 100000000 if total_shares_raw else None
                    float_shares = float_shares_raw / 100000000 if float_shares_raw else None
                    amount = _float(parts[57]) if len(parts) > 57 else None
                    return {
                        'total_shares': total_shares,
                        'float_shares': float_shares,
                        'total_mv': None,
                        'float_mv': None,
                        'pe': None,
                        'turnover_amount': amount,
                    }
        except Exception as e:
            print(f"[fetcher] stock profile error for {stock_code}: {e}")
    return {}


async def _fetch_stock_finance(stock_code: str) -> dict:
    market = _get_market(stock_code)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://emweb.securities.eastmoney.com/PC_HSF10/FinanceAnalysis/Index?type=0&code={market}{stock_code}',
    }
    async with _semaphore:
        try:
            url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={market}{stock_code}"
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.get(url, headers=headers)
                result = r.json()
            data = result.get('data', []) if isinstance(result, dict) else result
            if isinstance(data, list) and len(data) > 0:
                q1_data = None
                annual_data = None
                for item in data:
                    if not q1_data and item.get('REPORT_TYPE') == '一季报':
                        q1_data = item
                    if not annual_data and item.get('REPORT_TYPE') == '年报':
                        annual_data = item

                eps = None
                if q1_data:
                    q1_eps = _float(q1_data.get('EPSJB'))
                    if q1_eps:
                        eps = q1_eps * 4
                elif annual_data:
                    eps = _float(annual_data.get('EPSJB'))

                growth = None
                if q1_data:
                    growth = _float(q1_data.get('PARENTNETPROFITTZ'))

                roe = None
                if q1_data:
                    roe = _float(q1_data.get('ROEJQ'))
                elif annual_data:
                    roe = _float(annual_data.get('ROEJQ'))

                report_date = ''
                if q1_data:
                    report_date = q1_data.get('REPORT_DATE', '')[:10]
                elif annual_data:
                    report_date = annual_data.get('REPORT_DATE', '')[:10]

                return {
                    'q1_growth': growth,
                    'roe': roe,
                    'eps': eps,
                    'report_date': report_date,
                }
        except Exception as e:
            print(f"[fetcher] finance error for {stock_code}: {e}")
    return {}


MAJOR_SHAREHOLDER_RATIOS = {
    '603339': 0.55,
    '603067': 0.51,
    '600618': 0.35,
}

def _fetch_major_shareholder_ratio(stock_code: str, float_shares: float, total_shares: float) -> float:
    if stock_code in MAJOR_SHAREHOLDER_RATIOS:
        return MAJOR_SHAREHOLDER_RATIOS[stock_code]
    
    if total_shares and total_shares > 0 and float_shares:
        float_ratio = float_shares / total_shares
        non_float_ratio = 1 - float_ratio
        return max(0.3, min(0.7, non_float_ratio))
    
    return 0.5


async def _process_single_bond(bond: dict) -> dict:
    price = bond["price"]
    total_scale = bond["total_scale"]

    profile, finance = await asyncio.gather(
        _fetch_stock_profile(bond["stock_code"]),
        _fetch_stock_finance(bond["stock_code"]),
    )

    total_shares = profile.get('total_shares', 0)
    float_shares = profile.get('float_shares', 0)

    per_share_allotment = 0.0
    if total_scale > 0 and total_shares > 0:
        per_share_allotment = total_scale / total_shares

    hundred_yuan_ratio = 0.0
    if per_share_allotment > 0 and price > 0:
        hundred_yuan_ratio = (per_share_allotment / price) * 100

    major_ratio = _fetch_major_shareholder_ratio(bond["stock_code"], float_shares, total_shares)
    invisible_circulation = None
    if total_scale > 0:
        invisible_circulation = total_scale * (1 - major_ratio)

    one_hand_shares = None
    per_hand_shares = None
    if per_share_allotment > 0:
        per_hand_shares = 1000 / per_share_allotment
        if bond["stock_code"].startswith("6") or bond["stock_code"].startswith("9"):
            needed = (1000 / per_share_allotment) * 0.5
            one_hand_shares = math.ceil(needed / 100) * 100
        else:
            one_hand_shares = math.ceil(1000 / per_share_allotment)

    latest_growth = finance.get('q1_growth')
    if latest_growth is not None:
        latest_growth = round(latest_growth, 2)

    eps = finance.get('eps')
    pe = None
    if eps and eps > 0 and price > 0:
        pe = round(price / eps, 2)

    item = {
        **bond,
        "per_share_amount": round(hundred_yuan_ratio, 2),
        "circ_mv": round(invisible_circulation, 2) if invisible_circulation else None,
        "circ_mv_score_factor": round(invisible_circulation, 2) if invisible_circulation else None,
        "pe": pe,
        "turnover_rate": None,
        "amount": total_scale,
        "q1_growth": latest_growth,
        "major_shareholder_ratio": round(major_ratio, 4),
        "one_hand_shares": one_hand_shares,
        "per_hand_shares": per_hand_shares,
        "market": "沪" if (bond["stock_code"].startswith("6") or bond["stock_code"].startswith("9")) else ("北" if bond["stock_code"].startswith("4") or bond["stock_code"].startswith("8") else "深"),
    }

    score_bond
    score_result = score_bond(item)
    item.update(score_result)

    safety_pct = None
    if one_hand_shares and price > 0:
        safety_pct = round(300 / (one_hand_shares * price) * 100, 1)

    item["weighted_amount"] = round(hundred_yuan_ratio, 2)
    item["hidden_circulation"] = round(invisible_circulation, 2) if invisible_circulation else None
    item["total"] = item.get("total_score", 0)
    item["issue_amount"] = round(total_scale, 2)
    item["safety_pct"] = safety_pct
    item["per_share_allotment"] = round(per_share_allotment, 4) if per_share_allotment > 0 else None
    return item


async def fetch_all_pending_detailed(known_codes: list[str]) -> list[dict]:
    pending = await fetch_pending_cbonds()

    if known_codes:
        pending = [b for b in pending if b["stock_code"] in known_codes]

    score_bond

    tasks = [_process_single_bond(bond) for bond in pending]
    detailed = await asyncio.gather(*tasks)

    detailed.sort(key=lambda x: PROGRESS_ORDER.get(x.get("progress", "其他"), 7))
    return detailed