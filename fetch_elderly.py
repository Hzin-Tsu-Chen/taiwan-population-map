"""
抓取鄉鎮級「65 歲以上老年人口」

資料來源：內政部戶政司 ODRP052
          「現住人口數按性別、年齡及婚姻狀況」（村里級，2024 / 民國113）

註：年齡結構資料最新僅到 2024（民國113），2025 版尚未釋出。
    年齡以 5 歲分組，故取「65~69歲」以上所有組別加總。

作法：邊抓邊過濾（只留 65 歲以上組別）並直接加總到鄉鎮，避免將 200 萬筆全載入記憶體。

產出：elderly_by_town.csv（縣市, 鄉鎮, 65歲以上人口）
"""

import urllib.request
import json
import ssl
import csv
import re
from collections import defaultdict

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

YEAR = 113   # 民國113 = 2024（年齡結構最新可得年份）


def is_elderly(age_group):
    """
    判斷是否為 65 歲以上組別。
    age 值形如：'未滿15歲'、'15~19歲'、'65~69歲'、'100歲以上'
    """
    if age_group == '100歲以上':
        return True
    m = re.match(r'(\d+)~\d+歲', age_group or '')
    return m is not None and int(m.group(1)) >= 65


elderly = defaultdict(int)
page = 1
processed = 0

print('開始抓取 ODRP052（約 1031 頁）...')
while page <= 1500:
    url = f'https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP052/{YEAR}?page={page}'
    try:
        raw = urllib.request.urlopen(url, context=_ctx, timeout=60).read()
        rows = json.loads(raw.decode('utf-8-sig')).get('responseData')
    except Exception:
        try:   # 失敗重試一次
            raw = urllib.request.urlopen(url, context=_ctx, timeout=60).read()
            rows = json.loads(raw.decode('utf-8-sig')).get('responseData')
        except Exception:
            print(f'第 {page} 頁失敗，跳過')
            page += 1
            continue

    if not rows:
        break

    for r in rows:
        if not is_elderly(r.get('age')):
            continue
        site = r.get('site_id') or ''       # 例：新北市板橋區
        county, town = site[:3], site[3:]
        try:
            elderly[(county, town)] += int(r.get('population') or 0)
        except ValueError:
            pass

    processed += len(rows)
    if page % 100 == 0:
        print(f'  第 {page} 頁… 已處理 {processed:,} 筆')
    page += 1

with open('elderly_by_town.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['COUNTYNAME', 'TOWNNAME', 'Elderly65'])
    for (county, town), n in sorted(elderly.items()):
        w.writerow([county, town, n])

total = sum(elderly.values())
print(f'\n完成！共 {len(elderly)} 個鄉鎮')
print(f'全台 65 歲以上人口：{total:,}')
print(f'合理性檢查：應約 450 萬（老年人口占比約 19%）→ {"✓ 通過" if 4_000_000 < total < 5_000_000 else "✗ 數字異常，需檢查"}')
print('已存成 elderly_by_town.csv')
