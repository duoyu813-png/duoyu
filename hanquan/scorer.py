import math
from datetime import datetime, date
from typing import Optional

INDUSTRY_SCORES = {
    "AI": 8, "AI/科技": 8, "AI/半导体": 8, "半导体": 8,
    "芯片": 8, "AI芯片": 8, "AI服务器": 8,
    "AI应用": 5, "机器人": 5,
    "医药": 5, "生物医药": 5, "医疗器械": 5, "创新药": 5,
    "消费": 3, "食品饮料": 3, "白酒": 3,
    "新能源": 0, "光伏": -3, "锂电": -3, "锂电池": -3,
    "汽车": -5, "新能源汽车": -5, "整车": -5, "汽车零部件": -3,
    "地产": -5, "房地产": -5, "基建": -3,
    "化工": -3, "化工/材料": -3,
    "有色": -3, "有色/钢铁": -3, "钢铁": -3,
    "纺织服装": -2, "造纸": -2,
    "传统制造": -2, "机械设备": -1,
}

INDUSTRY_HOT_SCORES = {
    "AI": 5, "AI/科技": 5, "AI/半导体": 5, "半导体": 5, "芯片": 5,
    "AI芯片": 5, "AI服务器": 5, "AI应用": 5, "机器人": 5,
    "医药": 3, "生物医药": 3, "创新药": 3,
    "新能源": 2, "光伏": 1, "锂电": 1,
    "电力设备": 2,
}


def _find_industry_category(industry: str) -> str:
    for key in ["AI/科技", "AI/半导体", "半导体", "芯片", "AI芯片", "AI应用", "机器人",
                "医药", "生物医药", "创新药",
                "新能源", "光伏", "锂电", "锂电池",
                "汽车", "新能源汽车", "汽车零部件",
                "消费", "食品饮料",
                "地产/基建", "地产", "房地产",
                "化工/材料", "化工",
                "电力设备",
                "有色/钢铁", "有色", "钢铁",
                "机械设备",
                "纺织服装"]:
        if key in industry or industry in key:
            return key
    return industry


def score_hanquan(per_share: Optional[float]) -> int:
    if per_share is None or per_share <= 0:
        return 5
    if per_share <= 5:
        return 5
    if per_share <= 10:
        return 10
    if per_share <= 15:
        return 15
    if per_share <= 20:
        return 20
    if per_share <= 30:
        return 25
    return 30


def score_circ_mv(circ_mv: Optional[float]) -> int:
    if circ_mv is None or circ_mv <= 0:
        return 20
    if circ_mv <= 0.5:
        return 40
    if circ_mv <= 1:
        return 35
    if circ_mv <= 2:
        return 30
    if circ_mv <= 3:
        return 25
    if circ_mv <= 5:
        return 20
    if circ_mv <= 8:
        return 15
    if circ_mv <= 10:
        return 12
    if circ_mv <= 12:
        return 10
    if circ_mv <= 15:
        return 8
    return 4


def score_yeji(q1_growth: Optional[float]) -> int:
    if q1_growth is None:
        return 15
    if q1_growth >= 100:
        return 40
    if q1_growth >= 50:
        return 35
    if q1_growth >= 25:
        return 30
    if q1_growth >= 0:
        return 25
    if q1_growth >= -20:
        return 20
    if q1_growth >= -40:
        return 15
    if q1_growth >= -60:
        return 10
    return 5


def score_qianfu(registration_date: str, approval_date: str) -> int:
    ref = None
    for d in [approval_date, registration_date]:
        if d and len(d) >= 10:
            try:
                ref = datetime.strptime(d[:10], "%Y-%m-%d")
                break
            except ValueError:
                try:
                    ref = datetime.strptime(d[:10], "%Y-%m-%d")
                    break
                except ValueError:
                    pass
    if ref is None:
        return 0
    months = (datetime.now() - ref).days / 30.0
    if months <= 1.5:
        return 0
    extra = math.ceil(months - 1.5)
    return -min(extra * 3, 15)


def score_board_discount(board: str, circ_mv: Optional[float]) -> int:
    if board not in ("科创板", "创业板"):
        return 0
    cmv = circ_mv or 0
    if board == "科创板":
        if cmv <= 1:
            return -5
        if cmv <= 3:
            return -8
        if cmv <= 5:
            return -10
        return -15
    if board == "创业板":
        if cmv <= 1:
            return -3
        if cmv <= 3:
            return -5
        if cmv <= 5:
            return -8
        return -10
    return 0


def score_pe(pe: Optional[float], q1_growth: Optional[float]) -> int:
    if pe is None or pe <= 0:
        return 0
    if q1_growth and q1_growth > 30:
        return 0
    if pe > 200:
        return -10
    if pe > 100:
        return -5
    if pe > 50:
        return -3
    return 0


def score_industry(industry: str) -> int:
    cat = _find_industry_category(industry)
    return INDUSTRY_SCORES.get(cat, INDUSTRY_SCORES.get(industry, 0))


def score_industry_hot(industry: str) -> int:
    cat = _find_industry_category(industry)
    return INDUSTRY_HOT_SCORES.get(cat, INDUSTRY_HOT_SCORES.get(industry, 0))


def score_market_heat(amount: Optional[float]) -> int:
    if amount is None:
        return 0
    if amount >= 3:
        return 10
    if amount >= 2:
        return 5
    return 0


def determine_rating(total: int) -> str:
    if total >= 80:
        return "A+"
    if total >= 70:
        return "A"
    if total >= 60:
        return "B+"
    if total >= 50:
        return "B"
    if total >= 40:
        return "C"
    return "D"


def determine_action(bond: dict) -> dict:
    total = bond.get("total_score", 0)
    circ_mv = bond.get("circ_mv_score_factor") or 0
    industry = bond.get("industry", "")
    progress = bond.get("progress", "")
    price = bond.get("price") or 0
    per_share = bond.get("per_share_amount") or 0

    action = "观望"
    action_reason = ""
    action_price = ""

    if total >= 70:
        if "注册" in progress:
            action = "积极配债"
            action_reason = "高分+注册生效，可建仓潜伏"
        elif "证监" in progress or "批" in progress:
            action = "积极配债"
            action_reason = "高分+证监会批准，适合建仓"
        elif "交易" in progress or "通过" in progress:
            action = "关注建仓"
            action_reason = "高分+交易所通过，等待注册后加仓"
        else:
            action = "关注"
            action_reason = "高分标的，等待更明确信号"
    elif total >= 55:
        if circ_mv <= 3:
            if "注册" in progress:
                action = "关注"
                action_reason = "中高分+小流通盘，可参与"
            else:
                action = "关注"
                action_reason = "中高分+小流通盘，等待注册"
        else:
            action = "观望"
            action_reason = "中分，等待回调再入"
    else:
        if (_find_industry_category(industry) in ("AI/科技", "AI/半导体", "半导体", "芯片")):
            action = "小仓关注"
            action_reason = "低分但属AI板块，小仓参与"
        else:
            action = "回避"
            action_reason = "评分偏低，不建议参与"

    one_hand_shares = bond.get("one_hand_shares")
    safety_pct = None
    if one_hand_shares and price > 0:
        safety_pct = round(300 / (one_hand_shares * price) * 100, 1)
    
    if price > 0:
        if total >= 60:
            buy_line = round(price * 0.9, 2)
            sell_line = round(price * 1.15, 2)
        else:
            buy_line = round(price * 0.92, 2)
            sell_line = round(price * 1.1, 2)
        parts = []
        parts.append(f"买入≤{buy_line}")
        parts.append(f"卖出≥{sell_line}")
        if safety_pct is not None:
            parts.append(f"安全垫{safety_pct}%")
        action_price = " | ".join(parts)

    return {"action": action, "action_reason": action_reason, "action_price": action_price}


def score_bond(bond: dict) -> dict:
    per_share = bond.get("per_share_amount") or bond.get("each_allotment") or 0
    circ_mv = bond.get("circ_mv_score_factor") or bond.get("circ_mv") or 0
    industry = bond.get("industry", "")
    board = bond.get("board", "主板")
    price = bond.get("price") or 0
    pe = bond.get("pe")
    q1_growth = bond.get("q1_growth")
    amount = bond.get("amount") or 0
    reg_date = bond.get("registration_date", "")
    approval_date = bond.get("approval_date", "")

    s_hanquan = score_hanquan(per_share)
    s_circ = score_circ_mv(circ_mv)
    s_yeji = score_yeji(q1_growth)
    s_qianfu = score_qianfu(reg_date, approval_date)
    s_board = score_board_discount(board, circ_mv)
    s_pe = score_pe(pe, q1_growth)
    s_industry = score_industry(industry)
    s_heat_market = score_market_heat(amount)
    s_heat_industry = score_industry_hot(industry)

    total = max(0, s_hanquan + s_circ + s_yeji + s_qianfu + s_board + s_pe + s_industry + s_heat_market + s_heat_industry)

    rating = determine_rating(total)
    action_info = determine_action({
        **bond,
        "total_score": total,
        "circ_mv_score_factor": circ_mv,
    })

    details = {
        "含权量评分": {"score": s_hanquan, "max": 30, "factor": f"{per_share:.2f}元" if per_share else "暂无数据"},
        "隐形流通量评分": {"score": s_circ, "max": 40, "factor": f"{circ_mv:.2f}亿" if circ_mv else "暂无数据"},
        "业绩评分": {"score": s_yeji, "max": 40, "factor": f"Q1增长{q1_growth}%" if q1_growth is not None else "暂无数据"},
        "潜伏期扣分": {"score": s_qianfu, "max": 0, "factor": f"{reg_date[:7]}" if reg_date else "未知"},
        "300/688折扣": {"score": s_board, "max": 0, "factor": board},
        "PE扣分": {"score": s_pe, "max": 0, "factor": f"PE={pe}" if pe else "无"},
        "行业加减分": {"score": s_industry, "max": 10, "factor": industry},
        "市场热度": {"score": s_heat_market, "max": 10, "factor": f"成交{amount:.1f}亿" if amount and amount > 0 else "低"},
        "行业热度": {"score": s_heat_industry, "max": 5, "factor": industry},
    }

    return {
        "total_score": total,
        "rating": rating,
        "action": action_info["action"],
        "action_reason": action_info["action_reason"],
        "action_price": action_info["action_price"],
        "score_details": details,
    }