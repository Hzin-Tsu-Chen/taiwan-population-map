"""
選址分析資料抓取（ETL）：整合兩個政府開放資料來源，產出鄉鎮級選址指標

資料來源：
  1. 內政部戶政司 ODRP019（民國114 / 2025）— 村里級人口，程式加總至「鄉鎮」
  2. 衛福部食藥署 食品業者登錄資料 — 23 萬筆「餐飲場所」，由地址解析出鄉鎮

計算指標：
  - Population      鄉鎮人口
  - Restaurants     餐飲場所家數
  - PeoplePerStore  每家餐飲店可服務人口 = 人口 / 店數（越高 = 市場越未飽和）
  - PopChange       人口淨變化（2025 − 2024，正 = 成長中）

產出：town_indicators.csv
"""

import urllib.request
import json
import ssl
import csv
import re
import glob
from collections import defaultdict

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _field(row, zh, en):
    """相容中英文欄位名（不同年度 API 命名不一致）"""
    return row.get(zh) if zh in row else row.get(en)


def get_town_population(year):
    """村里級人口加總到『縣市+鄉鎮』"""
    pop = defaultdict(int)
    page = 1
    while page <= 50:
        url = f'https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP019/{year}?page={page}'
        raw = urllib.request.urlopen(url, context=_ctx, timeout=30).read()
        rows = json.loads(raw.decode('utf-8-sig')).get('responseData')
        if not rows:
            break
        for r in rows:
            site = _field(r, '區域別', 'site_id')       # 例：新北市板橋區
            county, town = site[:3], site[3:]
            people = (int(_field(r, '共同生活戶_男', 'household_ordinary_m'))
                      + int(_field(r, '共同生活戶_女', 'household_ordinary_f'))
                      + int(_field(r, '單獨生活戶_男', 'household_single_m'))
                      + int(_field(r, '單獨生活戶_女', 'household_single_f')))
            pop[(county, town)] += people
        page += 1
    return pop


def get_restaurant_counts(csv_path, valid_towns):
    """
    從食品業者登錄資料，數出每個鄉鎮的『餐飲場所』家數。

    地址解析採「已知鄉鎮名比對」而非正規表達式：
    因為鄉鎮名本身可能含「鄉鎮市區」字元（如「前鎮區」「新市區」），
    用 regex 會誤截成「前鎮」「新市」。改以最長名稱優先比對確保正確。
    """
    counts = defaultdict(int)
    # 依縣市建立鄉鎮名清單，長名優先（避免「新市」先於「新市區」被匹配）
    by_county = defaultdict(list)
    for county, town in valid_towns:
        by_county[county].append(town)
    for county in by_county:
        by_county[county].sort(key=len, reverse=True)

    unmatched = 0
    with open(csv_path, encoding='utf-8-sig', errors='replace') as fh:
        for row in csv.DictReader(fh):
            if row.get('登錄項目') != '餐飲場所':
                continue
            addr = (row.get('業者地址') or '').strip()
            county = addr[:3]                      # 台灣縣市名皆為 3 字
            rest = addr[3:]
            hit = None
            for town in by_county.get(county, []):
                if rest.startswith(town):
                    hit = town
                    break
            if hit:
                counts[(county, hit)] += 1
            else:
                unmatched += 1
    print(f'  地址無法對應鄉鎮：{unmatched:,} 筆')
    return counts


# 縣市名正規化：圖資與各來源用字不一（台/臺、桃園縣升格）
def norm_county(name):
    return name.replace('桃園縣', '桃園市').replace('台', '臺')


print('抓取鄉鎮人口（2025）...')
pop_2025 = get_town_population(114)
print('抓取鄉鎮人口（2024）...')
pop_2024 = get_town_population(113)

print('統計餐飲場所家數...')
food_csv = glob.glob('/tmp/food_data/*.csv')[0]
# 以人口資料的鄉鎮清單當作比對基準（同時涵蓋台/臺兩種寫法，因地址用字不一）
valid = set()
for (county, town) in pop_2025:
    valid.add((county, town))
    valid.add((county.replace('臺', '台'), town))   # 地址常寫「台」
restaurants = get_restaurant_counts(food_csv, valid)

# 餐飲統計的縣市用字正規化後彙總
store_by_town = defaultdict(int)
for (rc, rt), n in restaurants.items():
    store_by_town[(norm_county(rc), rt)] += n

# 組合指標
rows = []
for (county, town), population in pop_2025.items():
    c = norm_county(county)
    stores = store_by_town.get((c, town), 0)
    if population < 1000:      # 極小鄉鎮（如某些離島里）不納入，避免指標失真
        continue
    prev = pop_2024.get((county, town), 0)
    rows.append({
        'COUNTYNAME': c,
        'TOWNNAME': town,
        'Population': population,
        'Restaurants': stores,
        # 每家店服務人口：店數 0 時給一個上限值代表「完全空白市場」
        'PeoplePerStore': round(population / stores) if stores > 0 else population,
        'PopChange': population - prev,
    })

with open('town_indicators.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['COUNTYNAME', 'TOWNNAME', 'Population',
                                      'Restaurants', 'PeoplePerStore', 'PopChange'])
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: -r['Population']))

print(f'完成，共 {len(rows)} 個鄉鎮，已存成 town_indicators.csv')
