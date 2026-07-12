"""
餐飲業選址分析儀表板（RWD，支援手機）

回答的問題：「開一家餐飲店，全台哪個鄉鎮的市場最未飽和？」

分析模型（機會指數）：
    競爭分數（60%）：每店服務人口越多 → 市場越未飽和
    成長分數（40%）：人口淨變化越正 → 未來需求越好
    僅納入人口 ≥ 20,000 的鄉鎮（確保市場規模足夠）

資料：內政部戶政司人口（2025）+ 衛福部食藥署餐飲場所登錄（23 萬筆）
產出：index.html（RWD 儀表板）、site_map.html（地圖）
"""

import json
import geopandas as gpd
import pandas as pd
import folium
import branca.colormap as cm

MIN_POP = 20000   # 市場規模門檻


def normalize(name):
    """圖資用字正規化：桃園升格、台→臺"""
    return name.replace('桃園縣', '桃園市').replace('台', '臺')


# ── 1. 讀鄉鎮界線 + 指標，合併 ──
towns = gpd.read_file('town_boundaries.geojson')
towns['COUNTYNAME'] = towns['COUNTYNAME'].apply(normalize)
towns['geometry'] = towns.geometry.simplify(0.001)   # 簡化幾何，加快載入

ind = pd.read_csv('town_indicators.csv')
towns = towns.merge(ind, on=['COUNTYNAME', 'TOWNNAME'])
towns['NAME'] = towns['COUNTYNAME'] + towns['TOWNNAME']

# ── 2. 計算機會指數 ──
# 只評估市場規模足夠的鄉鎮
towns['Eligible'] = towns['Population'] >= MIN_POP
elig = towns[towns['Eligible']].copy()

# 競爭分數：每店服務人口的百分位（越高 = 店少人多 = 未飽和）
elig['CompScore'] = elig['PeoplePerStore'].rank(pct=True) * 100
# 成長分數：人口成長率的百分位
elig['GrowthRate'] = elig['PopChange'] / elig['Population'] * 100
elig['GrowScore'] = elig['GrowthRate'].rank(pct=True) * 100
# 綜合機會指數
elig['Opportunity'] = (elig['CompScore'] * 0.6 + elig['GrowScore'] * 0.4).round(1)

towns = towns.merge(
    elig[['NAME', 'CompScore', 'GrowScore', 'GrowthRate', 'Opportunity']],
    on='NAME', how='left')
towns[['Opportunity', 'GrowthRate']] = towns[['Opportunity', 'GrowthRate']].fillna(0)

# ── 3. 建地圖（機會指數著色）──
m = folium.Map(location=[23.7, 121], zoom_start=7, tiles='cartodbpositron')
colormap = cm.LinearColormap(['#d73027', '#fee08b', '#1a9850'], vmin=0, vmax=100)
colormap.caption = '選址機會指數（綠=藍海 / 紅=紅海）'

folium.GeoJson(
    towns,
    style_function=lambda f: {
        'fillColor': (colormap(f['properties']['Opportunity'])
                      if f['properties']['Population'] >= MIN_POP else '#e0e0e0'),
        'fillOpacity': 0.75, 'color': 'white', 'weight': 0.4},
    tooltip=folium.GeoJsonTooltip(fields=['NAME'], aliases=['']),
).add_to(m)
colormap.add_to(m)

# 點選鄉鎮 → 把資料傳給外層儀表板
click_js = f"""
<script>
setTimeout(function() {{
    function bind(g) {{
        g.eachLayer(function(l) {{
            if (l.feature && l.feature.properties) {{
                l.on('click', function() {{ parent.postMessage(l.feature.properties, '*'); }});
            }} else if (l.eachLayer) {{ bind(l); }}
        }});
    }}
    bind({m.get_name()});
}}, 800);
</script>
"""
m.get_root().html.add_child(folium.Element(click_js))
m.save('site_map.html')

# ── 4. 算儀表板要顯示的摘要與排名 ──
df = towns[towns['Eligible']].copy()
top10 = df.nlargest(10, 'Opportunity')       # 最推薦（藍海）
bottom5 = df.nsmallest(5, 'Opportunity')     # 最不推薦（紅海）

total_stores = int(towns['Restaurants'].sum())
total_pop = int(towns['Population'].sum())
national_pps = round(total_pop / total_stores)
best = top10.iloc[0]

detail = {r['NAME']: {
    'Population': f"{int(r['Population']):,}",
    'Restaurants': f"{int(r['Restaurants']):,}",
    'PeoplePerStore': f"{int(r['PeoplePerStore']):,}",
    'PopChange': f"{int(r['PopChange']):+,}",
    'Opportunity': (f"{r['Opportunity']:.1f}" if r['Population'] >= MIN_POP else '—'),
    'Verdict': ('未評估（人口未達 2 萬）' if r['Population'] < MIN_POP
                else '🟢 藍海，建議進場' if r['Opportunity'] >= 70
                else '🟡 中等' if r['Opportunity'] >= 40
                else '🔴 紅海，競爭激烈'),
} for _, r in towns.iterrows()}


def rank_html(frame, color):
    out = ''
    for i, (_, r) in enumerate(frame.iterrows(), 1):
        out += (f"<div class='rank-row'><span>{i}. {r['NAME']}</span>"
                f"<span style='color:{color};font-weight:600'>{r['Opportunity']:.0f}</span></div>")
    return out


# ── 5. 組出 RWD 儀表板 ──
html = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台灣餐飲業選址分析</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,"PingFang TC",sans-serif}
  body{background:#f0f2f5;color:#2c3e50;line-height:1.5}
  .header{background:#1a3a5c;color:#fff;padding:16px 20px}
  .header h1{font-size:20px}
  .header .q{font-size:14px;opacity:.9;margin-top:6px;font-weight:600}
  .header .src{font-size:11px;opacity:.7;margin-top:4px}
  .wrap{padding:16px 20px 24px;max-width:1400px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
  .card{background:#fff;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  .card .num{font-size:22px;font-weight:700;color:#1a3a5c}
  .card .lbl{font-size:12px;color:#7f8c8d;margin-top:3px}
  .main{display:grid;grid-template-columns:1fr 330px;gap:16px}
  .map-box{background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);height:600px}
  .map-box iframe{width:100%;height:100%;border:none}
  .side{display:flex;flex-direction:column;gap:14px}
  .panel{background:#fff;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  .panel h3{font-size:14px;margin-bottom:10px;color:#1a3a5c;border-left:4px solid #1a3a5c;padding-left:8px}
  .town{font-size:19px;font-weight:700;margin-bottom:8px}
  .verdict{font-size:14px;font-weight:600;margin-bottom:10px;padding:6px 10px;background:#f7f9fb;border-radius:6px}
  .row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #eee;font-size:13px}
  .row span:first-child{color:#7f8c8d}
  .row span:last-child{font-weight:600}
  .ph{color:#aaa;font-size:13px;text-align:center;padding:24px 0}
  .rank-row{display:flex;justify-content:space-between;padding:5px 0;font-size:13px;border-bottom:1px solid #f5f5f5}
  .note{margin-top:16px;background:#fff8e6;border-left:4px solid #f0ad4e;padding:12px;border-radius:6px;font-size:12px;color:#6b5b3e}
  /* ── 手機版 ── */
  @media(max-width:820px){
    .header h1{font-size:17px}
    .wrap{padding:12px}
    .main{grid-template-columns:1fr}
    .map-box{height:380px;order:1}
    .side{order:2}
    .card .num{font-size:19px}
  }
</style>
</head>
<body>
  <div class="header">
    <h1>台灣餐飲業選址分析</h1>
    <div class="q">💡 開一家餐飲店，哪一區的市場最未飽和？</div>
    <div class="src">資料：內政部戶政司人口（2025）× 衛福部食藥署餐飲場所登錄 __STORES__ 家｜點地圖鄉鎮看評估</div>
  </div>

  <div class="wrap">
    <div class="cards">
      <div class="card"><div class="num">__STORES__</div><div class="lbl">全台餐飲場所</div></div>
      <div class="card"><div class="num">__PPS__ 人</div><div class="lbl">全國平均每店服務人口</div></div>
      <div class="card"><div class="num">__TOWNS__</div><div class="lbl">評估鄉鎮數</div></div>
      <div class="card"><div class="num">__BEST__</div><div class="lbl">最推薦選址</div></div>
    </div>

    <div class="main">
      <div class="map-box"><iframe src="site_map.html"></iframe></div>
      <div class="side">
        <div class="panel" id="detail">
          <h3>📍 鄉鎮評估</h3>
          <div class="ph">點選地圖上的鄉鎮<br>查看選址評估</div>
        </div>
        <div class="panel">
          <h3>🟢 最推薦開店（藍海）</h3>
          __TOP__
        </div>
        <div class="panel">
          <h3>🔴 最不推薦（紅海）</h3>
          __BOTTOM__
        </div>
      </div>
    </div>

    <div class="note">
      <b>分析模型：</b>機會指數 = 競爭分數(60%) + 成長分數(40%)。競爭分數看「每店服務人口」（越多代表店少人多、市場未飽和）；成長分數看人口淨變化。僅評估人口 ≥ 2 萬的鄉鎮。<br>
      <b>已知限制：</b>本模型以「居住人口」為市場基礎，但商業區（如台中中區）有大量通勤消費人口，實際商機會被低估；郊區則可能因外食習慣較低而被高估。實務選址仍需搭配人流、租金與交通資料。
    </div>
  </div>

<script>
  var D = __DETAIL__;
  window.addEventListener('message', function(e){
    var n = e.data.NAME; if(!n || !D[n]) return;
    var d = D[n];
    document.getElementById('detail').innerHTML =
      '<h3>📍 鄉鎮評估</h3>' +
      '<div class="town">' + n + '</div>' +
      '<div class="verdict">' + d.Verdict + '</div>' +
      '<div class="row"><span>機會指數</span><span>' + d.Opportunity + '</span></div>' +
      '<div class="row"><span>人口</span><span>' + d.Population + '</span></div>' +
      '<div class="row"><span>現有餐飲店</span><span>' + d.Restaurants + ' 家</span></div>' +
      '<div class="row"><span>每店服務人口</span><span>' + d.PeoplePerStore + ' 人</span></div>' +
      '<div class="row"><span>人口淨變化</span><span>' + d.PopChange + '</span></div>';
  });
</script>
</body>
</html>"""

html = (html
        .replace('__STORES__', f'{total_stores:,}')
        .replace('__PPS__', f'{national_pps:,}')
        .replace('__TOWNS__', f"{int(df.shape[0])}")
        .replace('__BEST__', f"{best['NAME']}")
        .replace('__TOP__', rank_html(top10, '#27ae60'))
        .replace('__BOTTOM__', rank_html(bottom5, '#c0392b'))
        .replace('__DETAIL__', json.dumps(detail, ensure_ascii=False)))

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('=== 最推薦開店的鄉鎮（藍海）Top 10 ===')
print(top10[['NAME', 'Population', 'Restaurants', 'PeoplePerStore', 'Opportunity']].to_string(index=False))
print('\n儀表板已產生：index.html（支援手機 RWD）')
