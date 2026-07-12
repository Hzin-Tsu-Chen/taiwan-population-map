"""
台灣空間決策分析平台 —— 雙模組互動儀表板（RWD）

模組一｜餐飲業選址分析：「開一家餐飲店，哪一區的市場最未飽和？」
模組二｜長照資源缺口分析：「哪些地區的長照資源最不足，該優先增設？」

資料來源（皆為政府開放資料）：
  - 內政部戶政司：人口（2025）、年齡結構（2024）
  - 衛福部食藥署：餐飲場所登錄（22.8 萬筆）
  - 衛福部社家署：全國老人福利機構名冊（含核定床數）

產出：
  site_map.html / ltc_map.html  兩張地圖
  index.html                    整合儀表板（模組可切換、支援手機）
"""

import json
import geopandas as gpd
import pandas as pd
import folium
import branca.colormap as cm

MIN_POP = 20000       # 餐飲選址：市場規模門檻
MIN_ELDERLY = 3000    # 長照分析：老年人口門檻（避免極小鄉鎮失真）


def normalize(name):
    """圖資行政區正規化：桃園升格、台→臺"""
    return name.replace('桃園縣', '桃園市').replace('台', '臺')


# ══════════════════════════════════════════
# 1. 讀取與合併資料
# ══════════════════════════════════════════
towns = gpd.read_file('town_boundaries.geojson')
towns['COUNTYNAME'] = towns['COUNTYNAME'].apply(normalize)
towns['geometry'] = towns.geometry.simplify(0.001)      # 簡化幾何，加快載入

site = pd.read_csv('town_indicators.csv')               # 餐飲選址指標
ltc = pd.read_csv('ltc_indicators.csv')                 # 長照供需指標

towns = towns.merge(site, on=['COUNTYNAME', 'TOWNNAME'], how='inner')
towns = towns.merge(ltc.drop(columns=['Elderly65'], errors='ignore').assign(
    Elderly65=ltc['Elderly65']), on=['COUNTYNAME', 'TOWNNAME'], how='left')
towns['NAME'] = towns['COUNTYNAME'] + towns['TOWNNAME']
towns[['Elderly65', 'Institutions', 'Beds', 'BedsPer1000']] = \
    towns[['Elderly65', 'Institutions', 'Beds', 'BedsPer1000']].fillna(0)


# ══════════════════════════════════════════
# 2. 模組一：餐飲選址機會指數
#    競爭分數(60%) + 成長分數(40%)，僅評估人口 ≥ 2 萬的鄉鎮
# ══════════════════════════════════════════
elig = towns[towns['Population'] >= MIN_POP].copy()
elig['CompScore'] = (elig['PeoplePerStore'].rank(pct=True) * 100).round(1)
elig['GrowthRate'] = (elig['PopChange'] / elig['Population'] * 100).round(2)
elig['GrowScore'] = (elig['GrowthRate'].rank(pct=True) * 100).round(1)
elig['Opportunity'] = (elig['CompScore'] * 0.6 + elig['GrowScore'] * 0.4).round(1)

# 全國平均每店服務人口（作為競爭程度的比較基準）
national_pps = towns['Population'].sum() / towns['Restaurants'].sum()
elig['PpsRatio'] = (elig['PeoplePerStore'] / national_pps).round(2)   # 為全國平均的幾倍
n_evaluated = len(elig)

towns = towns.merge(
    elig[['NAME', 'Opportunity', 'CompScore', 'GrowScore', 'GrowthRate', 'PpsRatio']],
    on='NAME', how='left')
towns[['Opportunity', 'CompScore', 'GrowScore', 'GrowthRate', 'PpsRatio']] = \
    towns[['Opportunity', 'CompScore', 'GrowScore', 'GrowthRate', 'PpsRatio']].fillna(0)

# ══════════════════════════════════════════
# 3. 模組二：長照資源缺口指數
#    以「每千名老人床位數」相對全國平均計算缺口，僅評估老年人口 ≥ 3000 的鄉鎮
# ══════════════════════════════════════════
national_bp1k = towns['Beds'].sum() / towns['Elderly65'].sum() * 1000

ltc_elig = towns[towns['Elderly65'] >= MIN_ELDERLY].copy()
# 缺口指數：0=資源充足，100=完全空白。以全國平均為基準線
ltc_elig['GapIndex'] = (
    (1 - ltc_elig['BedsPer1000'].clip(upper=national_bp1k) / national_bp1k) * 100
).round(1)
# 缺口床數：要達到全國平均，還需要多少床
ltc_elig['BedShortfall'] = (
    (ltc_elig['Elderly65'] * national_bp1k / 1000 - ltc_elig['Beds']).clip(lower=0)
).round(0).astype(int)

towns = towns.merge(ltc_elig[['NAME', 'GapIndex', 'BedShortfall']], on='NAME', how='left')
towns[['GapIndex', 'BedShortfall']] = towns[['GapIndex', 'BedShortfall']].fillna(0)


# ══════════════════════════════════════════
# 4. 產生兩張地圖
# ══════════════════════════════════════════
def build_map(field, caption, colors, vmin, vmax, eligible_mask, filename):
    m = folium.Map(location=[23.7, 121], zoom_start=7, tiles='cartodbpositron')
    colormap = cm.LinearColormap(colors, vmin=vmin, vmax=vmax)
    colormap.caption = caption

    towns['_eligible'] = eligible_mask
    folium.GeoJson(
        towns,
        style_function=lambda f: {
            'fillColor': (colormap(f['properties'][field])
                          if f['properties']['_eligible'] else '#e0e0e0'),
            'fillOpacity': 0.75, 'color': 'white', 'weight': 0.4},
        tooltip=folium.GeoJsonTooltip(fields=['NAME'], aliases=['']),
    ).add_to(m)
    colormap.add_to(m)

    # 點選鄉鎮 → 傳資料給外層儀表板
    m.get_root().html.add_child(folium.Element(f"""
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
    </script>"""))
    m.save(filename)


# 餐飲選址：紅(紅海) → 綠(藍海)
build_map('Opportunity', '選址機會指數（綠=藍海 / 紅=紅海）',
          ['#d73027', '#fee08b', '#1a9850'], 0, 100,
          towns['Population'] >= MIN_POP, 'site_map.html')

# 長照缺口：綠(充足) → 紅(缺口大)
build_map('GapIndex', '長照資源缺口指數（紅=資源不足）',
          ['#1a9850', '#fee08b', '#d73027'], 0, 100,
          towns['Elderly65'] >= MIN_ELDERLY, 'ltc_map.html')


# ══════════════════════════════════════════
# 5. 儀表板資料（摘要卡、排名、點選詳情）
# ══════════════════════════════════════════
site_df = towns[towns['Population'] >= MIN_POP]
ltc_df = towns[towns['Elderly65'] >= MIN_ELDERLY]

stats = {
    'site': {
        'cards': [
            (f"{int(towns['Restaurants'].sum()):,}", '全台餐飲場所'),
            (f"{int(towns['Population'].sum() / towns['Restaurants'].sum())} 人", '平均每店服務人口'),
            (f"{len(site_df)}", '評估鄉鎮數'),
            (site_df.nlargest(1, 'Opportunity').iloc[0]['NAME'], '最推薦選址'),
        ],
        'top': site_df.nlargest(8, 'Opportunity')[['NAME', 'Opportunity']].values.tolist(),
        'bottom': site_df.nsmallest(5, 'Opportunity')[['NAME', 'Opportunity']].values.tolist(),
    },
    'ltc': {
        'cards': [
            (f"{int(towns['Beds'].sum()):,}", '全台長照床位'),
            (f"{national_bp1k:.1f} 床", '每千名老人床位（全國）'),
            (f"{int((ltc_df['Beds'] == 0).sum())}", '零床位鄉鎮（長照沙漠）'),
            (f"{int(ltc_df['BedShortfall'].sum()):,} 床", '達標尚缺床位'),
        ],
        'top': ltc_df.nlargest(8, 'BedShortfall')[['NAME', 'BedShortfall']].values.tolist(),
        'bottom': ltc_df.nsmallest(5, 'GapIndex')[['NAME', 'GapIndex']].values.tolist(),
    },
}

def site_reasoning(r):
    """產生選址評估的完整推理過程（所有數字皆為實際計算結果，可追溯驗證）"""
    if r['Population'] < MIN_POP:
        return '此鄉鎮人口未達 2 萬的評估門檻，市場規模不足，故不納入選址評估。'

    comp = ('市場明顯未飽和' if r['PpsRatio'] >= 1.5
            else '市場略未飽和' if r['PpsRatio'] > 1
            else '競爭較激烈')
    grow = ('人口成長中' if r['GrowthRate'] > 0.5
            else '人口穩定' if r['GrowthRate'] > -0.5
            else '人口流失中')

    return (
        f"<b>① 競爭程度（權重 60%）</b><br>"
        f"　每店服務 {int(r['PeoplePerStore'])} 人，是全國平均（{national_pps:.0f} 人）的 "
        f"<b>{r['PpsRatio']:.2f} 倍</b> → {comp}。<br>"
        f"　在 {n_evaluated} 個評估鄉鎮中排名百分位：<b>{r['CompScore']:.1f} 分</b><br><br>"

        f"<b>② 成長性（權重 40%）</b><br>"
        f"　人口成長率 <b>{r['GrowthRate']:+.2f}%</b> → {grow}。<br>"
        f"　排名百分位：<b>{r['GrowScore']:.1f} 分</b><br><br>"

        f"<b>③ 加權計算</b><br>"
        f"　{r['CompScore']:.1f} × 0.6 + {r['GrowScore']:.1f} × 0.4 = "
        f"<b>{r['Opportunity']:.1f} 分</b>"
    )


def ltc_reasoning(r):
    """產生長照缺口評估的推理過程"""
    if r['Elderly65'] < MIN_ELDERLY:
        return f"此鄉鎮 65 歲以上人口僅 {int(r['Elderly65']):,} 人，未達 3,000 人的評估門檻（樣本過小易失真），故不納入評估。"

    if r['Beds'] == 0:
        return (
            f"<b>① 需求端</b><br>　65 歲以上人口 <b>{int(r['Elderly65']):,} 人</b><br><br>"
            f"<b>② 供給端</b><br>　住宿式老人福利機構 <b>0 家、0 床</b><br><br>"
            f"<b>③ 缺口計算</b><br>"
            f"　若要達到全國平均（每千名老人 {national_bp1k:.1f} 床），<br>"
            f"　需增設 <b>{int(r['BedShortfall']):,} 床</b>。<br><br>"
            f"<span style='color:#c0392b'>※ 注意：此為「住宿式老人福利機構」統計，"
            f"不含護理之家與日照中心等其他長照服務。</span>"
        )

    ratio = r['BedsPer1000'] / national_bp1k
    level = ('高於全國平均' if ratio >= 1
             else '略低於全國平均' if ratio >= 0.7
             else '明顯低於全國平均')

    return (
        f"<b>① 需求端</b><br>　65 歲以上人口 <b>{int(r['Elderly65']):,} 人</b><br><br>"
        f"<b>② 供給端</b><br>　機構 {int(r['Institutions'])} 家、核定 <b>{int(r['Beds']):,} 床</b><br><br>"
        f"<b>③ 供給密度</b><br>"
        f"　每千名老人 <b>{r['BedsPer1000']:.1f} 床</b>（全國平均 {national_bp1k:.1f} 床）<br>"
        f"　= 全國平均的 <b>{ratio:.2f} 倍</b> → {level}<br><br>"
        f"<b>④ 缺口</b><br>　達全國平均尚缺 <b>{int(r['BedShortfall']):,} 床</b>"
    )


detail = {r['NAME']: {
    'Population': f"{int(r['Population']):,}",
    'Restaurants': f"{int(r['Restaurants']):,}",
    'PeoplePerStore': f"{int(r['PeoplePerStore']):,}",
    'Opportunity': f"{r['Opportunity']:.1f}" if r['Population'] >= MIN_POP else '—',
    'SiteVerdict': ('未評估（人口 < 2 萬）' if r['Population'] < MIN_POP
                    else '🟢 藍海，建議進場' if r['Opportunity'] >= 70
                    else '🟡 中等' if r['Opportunity'] >= 40 else '🔴 紅海，競爭激烈'),
    'SiteWhy': site_reasoning(r),
    'Elderly65': f"{int(r['Elderly65']):,}",
    'Institutions': f"{int(r['Institutions'])} 家",
    'Beds': f"{int(r['Beds']):,} 床",
    'BedsPer1000': f"{r['BedsPer1000']:.1f} 床",
    'BedShortfall': f"{int(r['BedShortfall']):,} 床",
    'LtcVerdict': ('未評估（老年人口 < 3000）' if r['Elderly65'] < MIN_ELDERLY
                   else '🔴 長照沙漠，急需增設' if r['Beds'] == 0
                   else '🟠 資源不足' if r['GapIndex'] >= 50
                   else '🟡 略低於平均' if r['GapIndex'] > 0 else '🟢 資源充足'),
    'LtcWhy': ltc_reasoning(r),
} for _, r in towns.iterrows()}


def rank_html(items, unit, color):
    """排行榜每一列都可點擊 —— 使用者不一定知道「橋頭區」在地圖上的位置，
    因此點文字就直接帶出該鄉鎮的評估面板。"""
    return ''.join(
        f"<div class='rank-row click' data-n='{name}' onclick=\"showTown('{name}')\" title='點我看評估過程'>"
        f"<span>{i}. {name} <span class='go'>▸</span></span>"
        f"<span style='color:{color};font-weight:600'>{val:,.0f}{unit}</span></div>"
        for i, (name, val) in enumerate(items, 1))


# ══════════════════════════════════════════
# 6. 組出 RWD 儀表板
# ══════════════════════════════════════════
html = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台灣空間決策分析平台</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,"PingFang TC",sans-serif}
  body{background:#f0f2f5;color:#2c3e50;line-height:1.5}
  .header{background:#1a3a5c;color:#fff;padding:16px 20px}
  .header h1{font-size:20px}
  .header .src{font-size:11px;opacity:.75;margin-top:5px}
  .modules{background:#fff;border-bottom:1px solid #e0e0e0;display:flex;overflow-x:auto}
  .mod{padding:14px 20px;cursor:pointer;font-size:14px;font-weight:600;color:#7f8c8d;border-bottom:3px solid transparent;white-space:nowrap}
  .mod.on{color:#1a3a5c;border-bottom-color:#1a3a5c;background:#f8fbff}
  .wrap{padding:16px 20px 24px;max-width:1400px;margin:0 auto}
  .q{background:#e8f4fd;border-left:4px solid #1a3a5c;padding:10px 14px;border-radius:6px;font-size:14px;font-weight:600;margin-bottom:14px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
  .card{background:#fff;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  .card .num{font-size:21px;font-weight:700;color:#1a3a5c}
  .card .lbl{font-size:12px;color:#7f8c8d;margin-top:3px}
  .main{display:grid;grid-template-columns:1fr 330px;gap:16px}
  .map-box{background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);height:580px}
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
  .why{margin-top:12px;padding:12px;background:#f8fbff;border-left:4px solid #1a3a5c;border-radius:6px;font-size:12.5px;line-height:1.75;color:#34495e}
  .why-title{font-weight:700;color:#1a3a5c;margin-bottom:8px;font-size:13px}
  .rank-row{display:flex;justify-content:space-between;padding:5px 0;font-size:13px;border-bottom:1px solid #f5f5f5}
  .rank-row.click{cursor:pointer;padding:6px 8px;margin:0 -8px;border-radius:5px;transition:background .12s}
  .rank-row.click:hover{background:#eaf3fb}
  .rank-row.sel{background:#dce9f5;font-weight:600}
  .rank-row .go{color:#95a5a6;font-size:11px}
  .rank-row.click:hover .go{color:#1a3a5c}
  .tip{font-size:11.5px;color:#7f8c8d;margin-bottom:8px}
  .note{margin-top:16px;background:#fff8e6;border-left:4px solid #f0ad4e;padding:12px;border-radius:6px;font-size:12px;color:#6b5b3e}
  .hide{display:none}
  @media(max-width:820px){
    .header h1{font-size:17px}
    .wrap{padding:12px}
    .main{grid-template-columns:1fr}
    .map-box{height:360px;order:1}
    .side{order:2}
    .card .num{font-size:18px}
  }
</style>
</head>
<body>
  <div class="header">
    <h1>台灣空間決策分析平台</h1>
    <div class="src">政府開放資料 × 空間分析｜內政部戶政司 · 衛福部食藥署 · 衛福部社家署</div>
  </div>

  <div class="modules">
    <div class="mod on" data-m="site" onclick="switchModule('site')">🍜 餐飲業選址分析</div>
    <div class="mod" data-m="ltc" onclick="switchModule('ltc')">🏥 長照資源缺口分析</div>
  </div>

  <div class="wrap">
    <!-- 模組一：餐飲選址 -->
    <div id="m-site">
      <div class="q">💡 開一家餐飲店，全台哪個鄉鎮的市場最未飽和？</div>
      <div class="cards">__SITE_CARDS__</div>
      <div class="main">
        <div class="map-box"><iframe src="site_map.html"></iframe></div>
        <div class="side">
          <div class="panel" id="siteDetail">
            <h3>📍 鄉鎮評估</h3>
            <div class="ph">點選地圖上的鄉鎮，<br>或點下方排行榜的名稱，<br>即可查看評估過程</div>
          </div>
          <div class="panel"><h3>🟢 最推薦開店（藍海）</h3><div class="tip">👆 點名稱看評估過程</div>__SITE_TOP__</div>
          <div class="panel"><h3>🔴 最不推薦（紅海）</h3><div class="tip">👆 點名稱看評估過程</div>__SITE_BOTTOM__</div>
        </div>
      </div>
      <div class="note">
        <b>分析模型：</b>機會指數 = 競爭分數(60%) + 成長分數(40%)。競爭分數看「每店服務人口」（越多代表店少人多、市場未飽和）；成長分數看人口淨變化。僅評估人口 ≥ 2 萬的鄉鎮。<br>
        <b>已知限制：</b>本模型以「居住人口」為市場基礎，但商業區（如臺中中區）有大量通勤消費人口，實際商機會被低估；郊區則可能因外食習慣較低而被高估。實務選址仍需搭配人流、租金與交通資料。
      </div>
    </div>

    <!-- 模組二：長照缺口 -->
    <div id="m-ltc" class="hide">
      <div class="q">💡 哪些地區的長照資源最不足，應優先增設機構？</div>
      <div class="cards">__LTC_CARDS__</div>
      <div class="main">
        <div class="map-box"><iframe src="ltc_map.html"></iframe></div>
        <div class="side">
          <div class="panel" id="ltcDetail">
            <h3>📍 鄉鎮評估</h3>
            <div class="ph">點選地圖上的鄉鎮，<br>或點下方排行榜的名稱，<br>即可查看評估過程</div>
          </div>
          <div class="panel"><h3>🔴 最缺床位（優先增設）</h3><div class="tip">👆 點名稱看評估過程</div>__LTC_TOP__</div>
          <div class="panel"><h3>🟢 資源最充足</h3><div class="tip">👆 點名稱看評估過程</div>__LTC_BOTTOM__</div>
        </div>
      </div>
      <div class="note">
        <b>分析模型：</b>以「每千名老人床位數」衡量長照供給密度，全國平均為 __BP1K__ 床。缺口指數 0 = 達全國平均，100 = 完全無床位。「達標尚缺床位」= 該鄉鎮達到全國平均水準所需增設的床數。僅評估老年人口 ≥ 3,000 的鄉鎮。<br>
        <b>資料範圍限制（重要）：</b>本資料為衛福部社家署「老人福利機構」名冊，屬<b>住宿式安養／養護機構</b>；<b>不含</b>護理之家（另依《護理機構法》管理）與長照2.0 的居家式／社區式服務（如日照中心）。因此「0 床」代表該鄉鎮無住宿式老人福利機構，<b>不等於完全沒有長照資源</b>。另，年齡結構資料最新僅至 2024 年。
      </div>
    </div>
  </div>

<script>
  var D = __DETAIL__;
  var current = 'site';

  function switchModule(m) {
    current = m;
    document.querySelectorAll('.mod').forEach(function(el) {
      el.classList.toggle('on', el.dataset.m === m);
    });
    document.getElementById('m-site').classList.toggle('hide', m !== 'site');
    document.getElementById('m-ltc').classList.toggle('hide', m !== 'ltc');
  }

  // 顯示某鄉鎮的評估 —— 地圖點選與排行榜點選共用此函式
  function showTown(n) {
    if (!n || !D[n]) return;
    var d = D[n];

    document.getElementById('siteDetail').innerHTML =
      '<h3>📍 鄉鎮評估</h3><div class="town">' + n + '</div>' +
      '<div class="verdict">' + d.SiteVerdict + '</div>' +
      '<div class="row"><span>機會指數</span><span>' + d.Opportunity + '</span></div>' +
      '<div class="row"><span>人口</span><span>' + d.Population + '</span></div>' +
      '<div class="row"><span>現有餐飲店</span><span>' + d.Restaurants + ' 家</span></div>' +
      '<div class="row"><span>每店服務人口</span><span>' + d.PeoplePerStore + ' 人</span></div>' +
      '<div class="why"><div class="why-title">🔍 為什麼是這個評估？</div>' + d.SiteWhy + '</div>';

    document.getElementById('ltcDetail').innerHTML =
      '<h3>📍 鄉鎮評估</h3><div class="town">' + n + '</div>' +
      '<div class="verdict">' + d.LtcVerdict + '</div>' +
      '<div class="row"><span>65歲以上人口</span><span>' + d.Elderly65 + '</span></div>' +
      '<div class="row"><span>長照機構</span><span>' + d.Institutions + '</span></div>' +
      '<div class="row"><span>核定床位</span><span>' + d.Beds + '</span></div>' +
      '<div class="row"><span>每千名老人床位</span><span>' + d.BedsPer1000 + '</span></div>' +
      '<div class="row"><span>達標尚缺床位</span><span>' + d.BedShortfall + '</span></div>' +
      '<div class="why"><div class="why-title">🔍 為什麼是這個評估？</div>' + d.LtcWhy + '</div>';

    // 標示目前選中的排行榜項目
    document.querySelectorAll('.rank-row').forEach(function(el) {
      el.classList.toggle('sel', el.dataset.n === n);
    });

    // 手機版面板在地圖下方，捲過去才看得到
    var panel = document.getElementById(current === 'site' ? 'siteDetail' : 'ltcDetail');
    if (window.innerWidth < 900) panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // 地圖上點選鄉鎮 → iframe 以 postMessage 通知
  window.addEventListener('message', function(e) {
    if (e.data && e.data.NAME) showTown(e.data.NAME);
  });
</script>
</body>
</html>"""

card_html = lambda cards: ''.join(
    f'<div class="card"><div class="num">{n}</div><div class="lbl">{l}</div></div>' for n, l in cards)

html = (html
        .replace('__SITE_CARDS__', card_html(stats['site']['cards']))
        .replace('__LTC_CARDS__', card_html(stats['ltc']['cards']))
        .replace('__SITE_TOP__', rank_html(stats['site']['top'], '', '#27ae60'))
        .replace('__SITE_BOTTOM__', rank_html(stats['site']['bottom'], '', '#c0392b'))
        .replace('__LTC_TOP__', rank_html(stats['ltc']['top'], ' 床', '#c0392b'))
        .replace('__LTC_BOTTOM__', rank_html(stats['ltc']['bottom'], '', '#27ae60'))
        .replace('__BP1K__', f'{national_bp1k:.1f}')
        .replace('__DETAIL__', json.dumps(detail, ensure_ascii=False)))

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('=== 模組一：餐飲選址（最推薦）===')
print(site_df.nlargest(5, 'Opportunity')[['NAME', 'PeoplePerStore', 'Opportunity']].to_string(index=False))
print(f'\n=== 模組二：長照缺口（最缺床位）===')
print(ltc_df.nlargest(5, 'BedShortfall')[['NAME', 'Elderly65', 'Beds', 'BedShortfall']].to_string(index=False))
print(f'\n全國平均每千名老人床位：{national_bp1k:.1f} 床')
print('\n儀表板已產生：index.html（雙模組、支援手機）')
