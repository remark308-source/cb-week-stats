#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股可轉債每日統計腳本
資料來源: 統一證券 CBAS 資訊網 (https://cbas16889.pscnet.com.tw)

流程：
1. 呼叫 API 取得所有已發行 CB 的即時資料
2. 過濾掉無效資料（市價為 0 或缺漏）
3. 與前一次存檔比對，若完全相同則判斷為「休市日」，跳過存檔
4. 計算 11 項統計指標
5. 儲存「當日明細快照」+ 更新「歷史彙總」
"""

import json
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd

# ============ 設定 ============
API_URL = "https://cbas16889.pscnet.com.tw/api/CbasQuote/GetIssuedCBSchedule"

# 專案根目錄下的 data 資料夾
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DETAIL_DIR = DATA_DIR / "details"
HISTORY_CSV = DATA_DIR / "history.csv"
LAST_HASH_FILE = DATA_DIR / ".last_hash"

# 價格區間分組（可依需求調整）
PRICE_BINS = [0, 95, 100, 105, 110, 120, float("inf")]
PRICE_BIN_LABELS = ["<95", "95~100", "100~105", "105~110", "110~120", ">=120"]
# CSV 欄位名稱用（避免特殊符號 <, >, ~ 造成欄位命名問題），順序須與上面的 LABELS 一一對應
PRICE_BIN_KEYS = ["lt95", "95_100", "100_105", "105_110", "110_120", "ge120"]

# 台灣時區
TW_TZ = timezone(timedelta(hours=8))


def fetch_raw_data() -> list[dict]:
    """呼叫 API 並回傳原始 CB 清單"""
    resp = requests.get(API_URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; CB-Stats-Bot/1.0)"
    })
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("message") != "QuerySuccess":
        raise RuntimeError(f"API 回傳異常訊息: {payload.get('message')}")

    result = payload.get("result")
    if not result:
        raise RuntimeError("API 回傳結果為空")

    return result


def to_float(value, default=None):
    """安全轉換字串為 float，轉換失敗回傳 default"""
    if value is None:
        return default
    try:
        s = str(value).strip()
        if s == "" or s == "-":
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def clean_data(raw: list[dict]) -> pd.DataFrame:
    """
    整理原始資料成 DataFrame，並過濾無效標的。
    無效標的定義：CB市價 (convertible_bond_market_price) 為 0 或缺漏，
    通常代表當天無成交/資料異常（例如轉換比例0%且無報價的新券）。
    """
    rows = []
    for item in raw:
        cb_price = to_float(item.get("convertible_bond_market_price"))
        conversion_value = to_float(item.get("conversion_value"))
        premium_rate = to_float(item.get("premium_rate"))
        conversion_price = to_float(item.get("conversion_price"))
        stock_price = to_float(item.get("underlying_stock_market_price"))

        # 過濾：CB市價缺漏或為0，視為無效資料，不納入統計
        if cb_price is None or cb_price <= 0:
            continue

        rows.append({
            "bond_code": item.get("bond_code"),
            "bond_name": item.get("underlying_bond"),
            "stock_code": item.get("convert_target_code"),
            "cb_price": cb_price,
            "conversion_price": conversion_price,
            "stock_price": stock_price,
            "conversion_value": conversion_value,
            "premium_rate": premium_rate,
        })

    df = pd.DataFrame(rows)
    return df


def compute_stats(df: pd.DataFrame, trade_date: str) -> dict:
    """計算11項統計指標"""
    if df.empty:
        raise RuntimeError("清理後的資料為空，無法計算統計指標")

    # 1. 價格區間標的數量分布
    price_bins = pd.cut(df["cb_price"], bins=PRICE_BINS, labels=PRICE_BIN_LABELS, right=False)
    price_distribution = price_bins.value_counts().sort_index().to_dict()

    # 2. CB平均價（收盤價，已核實 convertible_bond_market_price 即為收盤價）
    avg_cb_price = df["cb_price"].mean()

    # 3 & 4. PR75 / PR90 最高價：價格排序後，位於第75/90百分位「那一檔」的價格值
    pr75_price = df["cb_price"].quantile(0.75, interpolation="lower")
    pr90_price = df["cb_price"].quantile(0.90, interpolation="lower")

    # 5. 平均轉換價值
    avg_conversion_value = df["conversion_value"].mean()

    # 6. 平均轉換溢價率
    avg_premium_rate = df["premium_rate"].mean()

    # 7~11. 各條件數量統計
    count_cv_ge_100 = int((df["conversion_value"] >= 100).sum())
    count_cv_ge_120 = int((df["conversion_value"] >= 120).sum())
    count_premium_gt_0 = int((df["premium_rate"] > 0).sum())
    count_premium_ge_50 = int((df["premium_rate"] >= 50).sum())
    count_premium_ge_100 = int((df["premium_rate"] >= 100).sum())

    stats = {
        "date": trade_date,
        "total_count": len(df),
        "price_distribution": {str(k): int(v) for k, v in price_distribution.items()},
        "avg_cb_price": round(float(avg_cb_price), 2),
        "pr75_price": round(float(pr75_price), 2),
        "pr90_price": round(float(pr90_price), 2),
        "avg_conversion_value": round(float(avg_conversion_value), 2),
        "avg_premium_rate": round(float(avg_premium_rate), 2),
        "count_conversion_value_ge_100": count_cv_ge_100,
        "count_conversion_value_ge_120": count_cv_ge_120,
        "count_premium_rate_gt_0": count_premium_gt_0,
        "count_premium_rate_ge_50": count_premium_ge_50,
        "count_premium_rate_ge_100": count_premium_ge_100,
    }
    return stats


def compute_hash(raw: list[dict]) -> str:
    """對原始資料計算 hash，用來判斷今天資料是否跟昨天完全相同（可能是休市日）"""
    serialized = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def is_same_as_last_run(current_hash: str) -> bool:
    """比對本次資料 hash 與上次記錄的 hash 是否相同"""
    if not LAST_HASH_FILE.exists():
        return False
    last_hash = LAST_HASH_FILE.read_text(encoding="utf-8").strip()
    return last_hash == current_hash


def save_outputs(raw: list[dict], stats: dict, trade_date: str, current_hash: str):
    """儲存明細快照 + 更新歷史彙總 + 更新 hash 記錄"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 存當日明細快照（原始資料）
    detail_path = DETAIL_DIR / f"{trade_date}.json"
    detail_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[OK] 已儲存明細快照: {detail_path}")

    # 2. 更新歷史彙總 CSV（每天一行）
    flat_stats = {
        "date": stats["date"],
        "total_count": stats["total_count"],
        "avg_cb_price": stats["avg_cb_price"],
        "pr75_price": stats["pr75_price"],
        "pr90_price": stats["pr90_price"],
        "avg_conversion_value": stats["avg_conversion_value"],
        "avg_premium_rate": stats["avg_premium_rate"],
        "count_conversion_value_ge_100": stats["count_conversion_value_ge_100"],
        "count_conversion_value_ge_120": stats["count_conversion_value_ge_120"],
        "count_premium_rate_gt_0": stats["count_premium_rate_gt_0"],
        "count_premium_rate_ge_50": stats["count_premium_rate_ge_50"],
        "count_premium_rate_ge_100": stats["count_premium_rate_ge_100"],
    }
    # 把價格區間分布也攤平成欄位（用 KEYS 當欄位名稱，避免特殊符號）
    for label, key in zip(PRICE_BIN_LABELS, PRICE_BIN_KEYS):
        flat_stats[f"price_bin_{key}"] = stats["price_distribution"].get(label, 0)

    new_row = pd.DataFrame([flat_stats])

    if HISTORY_CSV.exists():
        history_df = pd.read_csv(HISTORY_CSV)
        # 避免同一天重複寫入：如果當天已存在，先移除舊的一列再補上新的
        history_df = history_df[history_df["date"] != trade_date]
        history_df = pd.concat([history_df, new_row], ignore_index=True)
    else:
        history_df = new_row

    history_df = history_df.sort_values("date").reset_index(drop=True)
    history_df.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")
    print(f"[OK] 已更新歷史彙總: {HISTORY_CSV}")

    # 3. 更新 hash 記錄
    LAST_HASH_FILE.write_text(current_hash, encoding="utf-8")


def main():
    trade_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    print(f"=== 開始執行可轉債每日統計 ({trade_date}) ===")

    try:
        raw = fetch_raw_data()
    except Exception as e:
        print(f"[ERROR] 抓取資料失敗: {e}", file=sys.stderr)
        sys.exit(1)

    current_hash = compute_hash(raw)

    if is_same_as_last_run(current_hash):
        print(f"[SKIP] 今天({trade_date})的資料跟上次執行完全相同，"
              f"判斷為休市日（國定假日/颱風假/週末補班等），不存檔。")
        sys.exit(0)

    df = clean_data(raw)
    print(f"[INFO] 原始筆數: {len(raw)}，過濾後有效筆數: {len(df)}")

    try:
        stats = compute_stats(df, trade_date)
    except Exception as e:
        print(f"[ERROR] 計算統計指標失敗: {e}", file=sys.stderr)
        sys.exit(1)

    print("[INFO] 統計結果:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    save_outputs(raw, stats, trade_date, current_hash)
    print("=== 執行完成 ===")


if __name__ == "__main__":
    main()
