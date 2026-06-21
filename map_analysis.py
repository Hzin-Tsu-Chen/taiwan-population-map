"""
台灣各縣市人口指標互動儀表板

版面：上方摘要數字卡 + 左側互動地圖 + 右側（點選詳情 + 排名榜）
資料：內政部戶政司開放資料 2025 / 民國114（由 fetch_data.py 產出 indicators.csv）

產出：
  - taiwan_map.html  純地圖（被儀表板嵌入）
  - index.html       完整儀表板（用瀏覽器打開這個）
"""

import json
import geopandas as gpd
import pandas as pd
import folium
import branca.colormap as cm


# ── 1. 讀地圖 + 清理名稱（桃園升格、臺正名）──
counties = gpd.read_file('taiwan.geojson')
counties['COUNTYNAME'] = counties['COUNTYNAME'].apply(
    lambda n: n.replace('桃園縣', '桃園市').replace('台', '臺'))

# ── 2. 讀指標、合併 ──
indicators = pd.read_csv('indicators.csv')
counties = counties.merge(indicators, on='COUNTYNAME')

# ── 2.5 簡化邊界幾何：縣市層級看不出差別，但大幅縮小檔案、加快載入 ──
counties['geometry'] = counties.geometry.simplify(0.005)

# ── 3. 算人口密度（先轉 TWD97 公尺座標系才能算面積）──
counties['Density'] = (counties['Population'] /
                       (counties.to_crs(epsg=3826).geometry.area / 1_000_000)).round(0)


# ── 4. 建地圖（單一底色：人口數；詳細數據都看右側儀表板）──
m = folium.Map(location=[23.7, 121], zoom_start=7, tiles='cartodbpositron')

colormap = cm.linear.YlOrRd_09.scale(counties['Population'].min(), counties['Population'].max())
colormap.caption = '人口數'
folium.GeoJson(
    counties,
    style_function=lambda feat, c=colormap: {
        'fillColor': c(feat['properties']['Population']), 'fillOpacity': 0.75,
        'color': 'gray', 'weight': 0.5},
    tooltip=folium.GeoJsonTooltip(fields=['COUNTYNAME'], aliases=['']),
).add_to(m)
colormap.add_to(m)

# ── 5. 注入 JS：點縣市時，把資料傳給外層儀表板 ──
map_name = m.get_name()
click_js = f"""
<script>
setTimeout(function() {{
    var map = {map_name};
    function bind(group) {{
        group.eachLayer(function(layer) {{
            if (layer.feature && layer.feature.properties) {{
                layer.on('click', function() {{
                    parent.postMessage(layer.feature.properties, '*');
                }});
            }} else if (layer.eachLayer) {{ bind(layer); }}
        }});
    }}
    bind(map);
}}, 800);
</script>
"""
m.get_root().html.add_child(folium.Element(click_js))
m.save('taiwan_map.html')


# ── 6. 算儀表板要顯示的摘要與排名 ──
df = counties[['COUNTYNAME', 'Population', 'Births', 'BirthRate', 'PopChange', 'Density']].copy()

total_pop = int(df['Population'].sum())
total_births = int(df['Births'].sum())
losing_count = int((df['PopChange'] < 0).sum())
lowest_birth = df.sort_values('BirthRate').iloc[0]

loss_rank = df.sort_values('PopChange').head(5)       # 人口流失前 5
gain_rank = df.sort_values('PopChange', ascending=False).head(5)  # 人口增加前 5

# 把每個縣市的完整資料變成 JS 物件（給點選詳情用，數字轉好讀格式）
detail_data = {r['COUNTYNAME']: {
    'Population': f"{int(r['Population']):,}",
    'Births': f"{int(r['Births']):,}",
    'BirthRate': f"{r['BirthRate']:.2f} ‰",
    'PopChange': f"{int(r['PopChange']):+,}",
    'Density': f"{int(r['Density']):,} 人/km²",
} for _, r in df.iterrows()}


def rank_rows(rank_df, color):
    rows = ""
    for i, (_, r) in enumerate(rank_df.iterrows(), 1):
        rows += (f"<div class='rank-row'><span>{i}. {r['COUNTYNAME']}</span>"
                 f"<span style='color:{color}'>{int(r['PopChange']):+,}</span></div>")
    return rows


# ── 7. 組出儀表板 index.html ──
html = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>台灣人口指標儀表板</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, "PingFang TC", sans-serif; }
  body { background: #f0f2f5; color: #2c3e50; }
  .header { background: #1a3a5c; color: white; padding: 16px 24px; }
  .header h1 { font-size: 22px; }
  .header p { font-size: 13px; opacity: 0.8; margin-top: 4px; }
  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 16px 24px; }
  .card { background: white; border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .card .num { font-size: 26px; font-weight: bold; color: #1a3a5c; }
  .card .lbl { font-size: 13px; color: #7f8c8d; margin-top: 4px; }
  .main { display: grid; grid-template-columns: 1fr 320px; gap: 16px; padding: 0 24px 24px; }
  .map-box { background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); height: 600px; }
  .map-box iframe { width: 100%; height: 100%; border: none; }
  .side { display: flex; flex-direction: column; gap: 16px; }
  .panel { background: white; border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .panel h3 { font-size: 15px; margin-bottom: 12px; color: #1a3a5c; border-left: 4px solid #1a3a5c; padding-left: 8px; }
  #detail .county-name { font-size: 20px; font-weight: bold; margin-bottom: 12px; }
  .detail-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #eee; font-size: 14px; }
  .detail-row span:first-child { color: #7f8c8d; }
  .detail-row span:last-child { font-weight: 600; }
  .placeholder { color: #aaa; font-size: 14px; text-align: center; padding: 30px 0; }
  .rank-row { display: flex; justify-content: space-between; padding: 6px 0; font-size: 14px; border-bottom: 1px solid #f5f5f5; }
</style>
</head>
<body>
  <div class="header">
    <h1>台灣各縣市人口指標儀表板（2025 / 民國114年）</h1>
    <p>資料來源：內政部戶政司開放資料｜人口為 2025 年底數，出生為 2025 年按發生日期統計｜點選地圖縣市看詳情</p>
  </div>

  <div class="cards">
    <div class="card"><div class="num">__TOTAL_POP__</div><div class="lbl">全台總人口</div></div>
    <div class="card"><div class="num">__TOTAL_BIRTHS__</div><div class="lbl">全年總出生數</div></div>
    <div class="card"><div class="num">__LOSING__ 個</div><div class="lbl">人口負成長縣市</div></div>
    <div class="card"><div class="num">__LOWBIRTH__</div><div class="lbl">出生率最低</div></div>
  </div>

  <div class="main">
    <div class="map-box"><iframe src="taiwan_map.html"></iframe></div>
    <div class="side">
      <div class="panel" id="detail">
        <h3>📍 縣市詳情</h3>
        <div class="placeholder">點選地圖上的縣市<br>查看詳細數據</div>
      </div>
      <div class="panel">
        <h3>📉 人口流失最多</h3>
        __LOSS_RANK__
      </div>
      <div class="panel">
        <h3>📈 人口增加最多</h3>
        __GAIN_RANK__
      </div>
    </div>
  </div>

<script>
  var DETAIL = __DETAIL_JSON__;
  window.addEventListener('message', function(e) {
    var name = e.data.COUNTYNAME;
    if (!name || !DETAIL[name]) return;
    var d = DETAIL[name];
    document.getElementById('detail').innerHTML =
      '<h3>📍 縣市詳情</h3>' +
      '<div class="county-name">' + name + '</div>' +
      '<div class="detail-row"><span>人口</span><span>' + d.Population + '</span></div>' +
      '<div class="detail-row"><span>出生數</span><span>' + d.Births + '</span></div>' +
      '<div class="detail-row"><span>出生率</span><span>' + d.BirthRate + '</span></div>' +
      '<div class="detail-row"><span>人口淨變化</span><span>' + d.PopChange + '</span></div>' +
      '<div class="detail-row"><span>人口密度</span><span>' + d.Density + '</span></div>';
  });
</script>
</body>
</html>"""

html = (html
        .replace('__TOTAL_POP__', f"{total_pop:,}")
        .replace('__TOTAL_BIRTHS__', f"{total_births:,}")
        .replace('__LOSING__', str(losing_count))
        .replace('__LOWBIRTH__', f"{lowest_birth['COUNTYNAME']} {lowest_birth['BirthRate']:.1f}‰")
        .replace('__LOSS_RANK__', rank_rows(loss_rank, '#c0392b'))
        .replace('__GAIN_RANK__', rank_rows(gain_rank, '#27ae60'))
        .replace('__DETAIL_JSON__', json.dumps(detail_data, ensure_ascii=False)))

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('儀表板已產生！用瀏覽器打開 index.html')
