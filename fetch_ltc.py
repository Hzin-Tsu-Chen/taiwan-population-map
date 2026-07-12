"""
抓取長照（老人福利）機構資料，並整合成鄉鎮級的長照供需指標

資料來源：
  1. 衛福部社家署「全國老人福利機構名冊」（data.gov.tw #8572）
     - 分 22 縣市各一個 CSV，編碼為 Big5
     - 含機構名稱、地址、核定床數
  2. elderly_by_town.csv（由 fetch_elderly.py 產出，65 歲以上人口）

計算指標：
  - Institutions   機構家數
  - Beds           核定床位數
  - BedsPer1000    每千名老人可用床位數 = 床位 / 老年人口 × 1000
                   （此為長照供給密度的標準指標，越低代表資源越不足）

產出：ltc_indicators.csv
"""

import urllib.request
import json
import ssl
import csv
import io
from collections import defaultdict

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

DATASET_API = 'https://data.gov.tw/api/v2/rest/dataset/8572'

# data.gov.tw 會擋沒有 User-Agent 的請求（回 403），故需帶瀏覽器標頭
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'}


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=_HEADERS)
    return urllib.request.urlopen(req, context=_ctx, timeout=timeout).read()


def normalize_county(name):
    """縣市用字正規化：官方用「臺」，部分資料寫「台」"""
    return name.replace('台', '臺')


def load_valid_towns():
    """以老年人口資料的鄉鎮清單為基準（用於地址解析比對）"""
    towns = defaultdict(list)
    with open('elderly_by_town.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            county = normalize_county(r['COUNTYNAME'])
            towns[county].append(r['TOWNNAME'])
            # 地址常寫「台」，兩種寫法都要能比對
            towns[county.replace('臺', '台')].append(r['TOWNNAME'])
    # 長名優先，避免「新市」先於「新市區」被匹配
    for c in towns:
        towns[c] = sorted(set(towns[c]), key=len, reverse=True)
    return towns


def parse_town(address, valid_towns):
    """從地址解析出（縣市, 鄉鎮）。以已知鄉鎮名比對，不用正規表達式猜。"""
    addr = (address or '').strip()
    if len(addr) < 4:
        return None
    county = addr[:3]
    for town in valid_towns.get(county, []):
        if addr[3:].startswith(town):
            return normalize_county(county), town
    return None


# ── 1. 取得 22 個縣市的 CSV 下載網址 ──
print('取得長照機構資料集清單...')
meta = json.loads(fetch(DATASET_API, timeout=30).decode('utf-8-sig'))
csv_urls = [d['resourceDownloadUrl'] for d in meta['result']['distribution']
            if d.get('resourceFormat') == 'CSV']
print(f'  共 {len(csv_urls)} 個縣市檔案')

# ── 2. 逐檔抓取並解析（Big5 編碼）──
valid_towns = load_valid_towns()
stats = defaultdict(lambda: {'inst': 0, 'beds': 0})
unmatched = 0

print('抓取並解析各縣市機構資料...')
for url in csv_urls:
    raw = fetch(url)
    text = raw.decode('big5', errors='replace')     # 政府舊資料常用 Big5
    for row in csv.DictReader(io.StringIO(text)):
        hit = parse_town(row.get('地址'), valid_towns)
        if not hit:
            unmatched += 1
            continue
        beds_raw = (row.get('核定床數') or '0').strip().replace(',', '')
        try:
            beds = int(float(beds_raw))
        except ValueError:
            beds = 0
        stats[hit]['inst'] += 1
        stats[hit]['beds'] += beds

print(f'  地址無法對應鄉鎮：{unmatched} 筆')

# ── 3. 與老年人口合併，計算供給密度 ──
rows = []
with open('elderly_by_town.csv', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        county = normalize_county(r['COUNTYNAME'])
        town = r['TOWNNAME']
        elderly = int(r['Elderly65'])
        s = stats.get((county, town), {'inst': 0, 'beds': 0})
        rows.append({
            'COUNTYNAME': county,
            'TOWNNAME': town,
            'Elderly65': elderly,
            'Institutions': s['inst'],
            'Beds': s['beds'],
            # 每千名老人床位數（老年人口為 0 的極小鄉鎮給 0，避免除以零）
            'BedsPer1000': round(s['beds'] / elderly * 1000, 1) if elderly > 0 else 0,
        })

with open('ltc_indicators.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['COUNTYNAME', 'TOWNNAME', 'Elderly65',
                                      'Institutions', 'Beds', 'BedsPer1000'])
    w.writeheader()
    w.writerows(rows)

total_inst = sum(r['Institutions'] for r in rows)
total_beds = sum(r['Beds'] for r in rows)
total_elderly = sum(r['Elderly65'] for r in rows)
print(f'\n完成！共 {len(rows)} 個鄉鎮')
print(f'  長照機構：{total_inst:,} 家')
print(f'  核定床位：{total_beds:,} 床')
print(f'  65歲以上人口：{total_elderly:,}')
print(f'  全國平均：每千名老人 {total_beds / total_elderly * 1000:.1f} 床')
print('已存成 ltc_indicators.csv')
