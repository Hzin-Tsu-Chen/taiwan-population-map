"""
台灣各縣市人口指標互動地圖（多指標可切換）

資料來源：
  - 地理界線：g0v 開放圖資（2010 縣市界線，邊界穩定故沿用）
  - 各項指標：內政部戶政司開放資料 API（2024 / 民國113），由 fetch_data.py 產出 indicators.csv

可切換指標：
  - 人口數
  - 出生率（每千人）
  - 人口淨變化（2024 − 2023，紅=流失 / 綠=增加）

產出：taiwan_map.html
"""

import geopandas as gpd
import pandas as pd
import folium
import branca.colormap as cm


# ── 1. 讀地圖界線 ──
counties = gpd.read_file('taiwan.geojson')

# ── 1.5 資料清理：2010 圖資名稱更新到現況 ──
# (1) 桃園 2014 升格：桃園縣→桃園市  (2) 官方正名：台→臺
def normalize_name(name):
    return name.replace('桃園縣', '桃園市').replace('台', '臺')

counties['COUNTYNAME'] = counties['COUNTYNAME'].apply(normalize_name)

# ── 2. 讀指標資料（由 fetch_data.py 從政府 API 抓取計算）──
indicators = pd.read_csv('indicators.csv')

# ── 3. 合併地圖 + 指標（用縣市名當鑰匙，即 JOIN）──
counties = counties.merge(indicators, on='COUNTYNAME')

# ── 4. 算人口密度（需先轉 TWD97 公尺座標系才能算面積）──
counties['Area_km2'] = counties.to_crs(epsg=3826).geometry.area / 1_000_000
counties['Density'] = (counties['Population'] / counties['Area_km2']).round(0)


# ── 5. 建立多指標互動地圖 ──
m = folium.Map(location=[23.7, 121], zoom_start=7, tiles='cartodbpositron')

# 每個指標的設定：(欄位, 圖層名, 配色, 是否預設顯示)
layers = [
    ('Population', '人口數',          cm.linear.YlOrRd_09, True),
    ('BirthRate',  '出生率(‰)',      cm.linear.YlGnBu_09, False),
    ('PopChange',  '人口淨變化',      None,                 False),  # 特殊：紅綠發散配色
]

for field, label, palette, show in layers:
    vmin, vmax = counties[field].min(), counties[field].max()

    if field == 'PopChange':
        # 紅(流失)→白(持平)→綠(增加)，以 0 為中心
        m_abs = max(abs(vmin), abs(vmax))
        colormap = cm.LinearColormap(['red', 'white', 'green'], vmin=-m_abs, vmax=m_abs)
    else:
        colormap = palette.scale(vmin, vmax)
    colormap.caption = label

    fg = folium.FeatureGroup(name=label, overlay=False, show=show)

    folium.GeoJson(
        counties,
        style_function=lambda feat, f=field, c=colormap: {
            'fillColor': c(feat['properties'][f]),
            'fillOpacity': 0.75,
            'color': 'gray',
            'weight': 0.5,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=['COUNTYNAME', 'Population', 'BirthRate', 'PopChange', 'Density'],
            aliases=['縣市:', '人口:', '出生率(‰):', '人口淨變化:', '密度(人/km²):'],
        ),
    ).add_to(fg)

    fg.add_to(m)
    colormap.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
m.save('taiwan_map.html')
print('多指標互動地圖已存成 taiwan_map.html')
print('可切換：人口數 / 出生率 / 人口淨變化')
