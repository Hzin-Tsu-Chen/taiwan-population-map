"""
資料抓取（ETL）：從內政部戶政司開放資料 API 抓取並計算各縣市指標

抓取項目（內政部戶政司開放資料）：
  - 人口數：ODRP019 / 民國114（2025）
  - 出生數：ODRP056 / 民國114（2025，按發生日期）
  - 前一年人口：ODRP019 / 民國113（2024），用來算人口淨變化（2025−2024）

註：2025 出生為「按發生日期」統計（API 尚未提供按登記日期版），
    故總數約 105,676，略低於官方「按登記」headline 107,812。

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


def get_births(year, dataset):
    """各縣市出生數（鄉鎮市區級加總）。不同年度資料集/欄位名不同，故傳入 dataset"""
    births = defaultdict(int)
    for r in _fetch_pages(dataset, year):
        county = _field(r, '區域別', 'site_id')[:3]
        births[county] += int(_field(r, '嬰兒出生數', 'birth_count'))
    return births


# 年度設定（民國年）：以 2025 為主，2024 為前一年（算人口淨變化）
CUR, PREV = 114, 113   # 2025, 2024

print('抓取人口（2025）...')
pop_cur = get_population(CUR)
print('抓取人口（2024）...')
pop_prev = get_population(PREV)
print('抓取出生數（2025，按發生日期）...')
births_cur = get_births(CUR, 'ODRP056')   # 2025 出生（ODRP056，按發生日期）

# 組合所有指標
rows = []
for county, population in pop_cur.items():
    births = births_cur.get(county, 0)
    rows.append({
        'COUNTYNAME': county,
        'Population': population,
        'Births': births,
        'BirthRate': round(births / population * 1000, 2),
        'PopChange': population - pop_prev.get(county, 0),
    })

with open('indicators.csv', 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['COUNTYNAME', 'Population', 'Births', 'BirthRate', 'PopChange'])
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda r: -r['Population']))

print(f'完成，共 {len(rows)} 縣市，已存成 indicators.csv')
