"""公共 ACTIONS 看板环境配置（仅环境变量，无硬编码密钥）"""
import os


class Config:
    # 集思录 cookie（可选）：游客仅前 20 条，填 Cookie 可解锁全部
    JISILU_COOKIE = os.environ.get("JISILU_COOKIE", "")

    # GitHub Actions 云端 YTM 兜底：无本地数据库，全部用东财下发数据
    PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")