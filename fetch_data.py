"""
資料抓取（ETL）：從內政部戶政司開放資料 API 抓取並計算各縣市指標

抓取項目（皆為 2024 / 民國113 年官方資料）：
  - 人口數（ODRP019，村里級加總至縣市）
  - 出生數（ODRP055，鄉鎮市區級加總至縣市）
  - 前一年人口（ODRP019 / 112，用來算人口淨變化）

計算指標：
  - BirthRate  出生率（每千人）= 出生數 / 人口 × 1000
  - PopChange  人口淨變化 = 2024人口 − 2023人口（正=增加，負=流失）

產出：indicators.csv
"""

import urllib.request
import json
import ssl
import csv
from collections import defaultdict

# 政府網站憑證較舊，關閉驗證以利下載
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _fetch_pages(dataset, year):
    """抓某資料集某年度的全部分頁，回傳所有資料列（抓到沒資料為止）"""
    all_rows = []
    page = 1
    while page <= 50:  # 安全上限，避免無限迴圈
        url = f'https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}?page={page}'
        raw = urllib.request.urlopen(url, context=_ctx, timeout=30).read()
        rows = json.loads(raw.decode('utf-8-sig')).get('responseData')
        if not rows:
            break
        all_rows += rows
        page += 1
    return all_rows


def _field(row, zh, en):
    """相容中英文欄位名（不同年度的 API 欄位命名不一致）"""
    return row.get(zh) if zh in row else row.get(en)


def get_population(year):
    """各縣市人口數（村里級加總）"""
    pop = defaultdict(int)
    for r in _fetch_pages('ODRP019', year):
        county = _field(r, '區域別', 'site_id')[:3]
        pop[county] += (int(_field(r, '共同生活戶_男', 'household_ordinary_m'))
                        + int(_field(r, '共同生活戶_女', 'household_ordinary_f'))
                        + int(_field(r, '單獨生活戶_男', 'household_single_m'))
                        + int(_field(r, '單獨生活戶_女', 'household_single_f')))
    return pop


def get_births(year):
    """各縣市出生數（鄉鎮市區級加總）"""
    births = defaultdict(int)
    for r in _fetch_pages('ODRP055', year):
        county = r['區域別'][:3]
        births[county] += int(r['嬰兒出生數'])
    return births


print('抓取人口（2024）...')
pop_2024 = get_population(113)
print('抓取人口（2023）...')
pop_2023 = get_population(112)
print('抓取出生數（2024）...')
births_2024 = get_births(113)

# 組合所有指標
rows = []
for county, population in pop_2024.items():
    births = births_2024.get(county, 0)
    rows.append({
        'COUNTYNAME': county,
        'Population': population,
        'Births': births,
        'BirthRate': round(births / population * 1000, 2),
        'PopChange': population - pop_2023.get(county, 0),
    })

with open('indicators.csv', 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['COUNTYNAME', 'Population', 'Births', 'BirthRate', 'PopChange'])
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda r: -r['Population']))

print(f'完成，共 {len(rows)} 縣市，已存成 indicators.csv')
