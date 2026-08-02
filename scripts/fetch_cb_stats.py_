import os, requests
from datetime import datetime
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.tpex.org.tw/"
}

def _get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] 抓取失敗 {url}: {e}")
        return []

def fetch_data():
    # 1. 抓取行情與發行資料
    quotes_raw = _get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
    issbd = _get("https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data")

    # 2. 建立 CB 代碼白名單
    cb_code_set = { (x.get("BondCode") or "").strip() for x in issbd if x.get("BondCode") }
    
    # 3. 篩選出真正的 CB 行情
    cb_quotes = [x for x in quotes_raw if (x.get("SecuritiesCompanyCode") or "").strip() in cb_code_set]

    # ========== 🔍 診斷資訊區塊 ==========
    print("\n" + "="*50)
    print("🔍 [診斷資訊] 第一筆 CB 的原始資料內容：")
    if cb_quotes:
        first_cb = cb_quotes[0]
        for key, value in first_cb.items():
            print(f"欄位名: {key!r}  |  數值: {value!r}")
    else:
        print("❌ 警告：在即時行情中找不到任何屬於 CB 的代碼！")
    print("="*50 + "\n")
    # ===================================

    return cb_quotes

def _to_float(s):
    try:
        v = float(str(s or "").replace(",", ""))
        return v if v > 0 else None
    except:
        return None

if __name__ == "__main__":
    cb_list = fetch_data()
    # 確保產生一個 index.html 讓 Actions 部署不會失敗
    os.makedirs("dist", exist_ok=True)
    with open("dist/index.html", "w") as f: f.write("<h1>Diagnostic Mode</h1>")
    print(f"診斷腳本執行完畢，共找到 {len(cb_list)} 檔 CB")
