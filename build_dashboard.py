"""
台灣空間決策分析平台 —— 雙模組互動儀表板（RWD）

模組一｜餐飲業選址分析：「開一家餐飲店，哪一區的市場最未飽和？」
模組二｜長照資源缺口分析：「哪些地區的長照資源最不足，該優先增設？」

資料來源（皆為政府開放資料）：
  - 內政部戶政司：人口（2025）、年齡結構（2024）
  - 衛福部食藥署：餐飲場所登錄（23.2 萬家）
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


# ══════════════════════════════════════════
# 1. 讀取與合併資料
# ══════════════════════════════════════════
#
# 界線圖資使用內政部國土測繪中心「鄉鎮市區界線(TWD97經緯度)」官方版（民國114/03/18）。
#
# 【踩過的坑，特此記錄】原先採用的是社群流傳的舊版 GeoJSON，行政區名稱停留在改制前
# （中壢「市」、蘆竹「鄉」…），與戶政司的現行名稱 join 不起來，導致整個桃園市 13 個區
# 靜默消失於地圖之外（234 萬人、2.7 萬家餐飲店）；且該圖資把那瑪夏區誤標為「三民區」，
# 而高雄真正的三民區根本不存在。改用官方版後兩個問題一併消失，
# 下方的合併完整性檢查則確保同類錯誤不會再無聲發生。
towns = gpd.read_file('town_boundaries.geojson')
towns['geometry'] = towns.geometry.simplify(0.001)      # 簡化幾何，加快載入

site = pd.read_csv('town_indicators.csv')               # 餐飲選址指標
ltc = pd.read_csv('ltc_indicators.csv')                 # 長照供需指標

towns = towns.merge(site, on=['COUNTYNAME', 'TOWNNAME'], how='inner')
towns = towns.merge(ltc.drop(columns=['Elderly65'], errors='ignore').assign(
    Elderly65=ltc['Elderly65']), on=['COUNTYNAME', 'TOWNNAME'], how='left')
towns['NAME'] = towns['COUNTYNAME'] + towns['TOWNNAME']

# ── 合併完整性檢查 ──────────────────────────────────────────
# 這是內連接：任何行政區名稱對不上的鄉鎮都會被靜默丟棄。
# （曾因圖資使用桃園升格前的舊地名，整個桃園市 13 個區無聲消失。）
# 故此處驗證：合併後的統計數字必須等於原始檔的總計，否則直接中止。
lost_rows = len(site) - len(towns)
lost_rest = int(site['Restaurants'].sum() - towns['Restaurants'].sum())
if lost_rows or lost_rest:
    missing = set(zip(site.COUNTYNAME, site.TOWNNAME)) - set(zip(towns.COUNTYNAME, towns.TOWNNAME))
    raise SystemExit(
        f'✗ 合併遺漏 {lost_rows} 個鄉鎮、{lost_rest:,} 家餐飲店。'
        f'未匹配的行政區：{sorted(missing)}\n'
        f'  可能原因：界線圖資的行政區名稱與戶政司資料不一致（如行政區改制未更新）。')
print(f'✓ 合併完整性檢查通過：{len(towns)} 個鄉鎮、'
      f"{int(towns['Restaurants'].sum()):,} 家餐飲店、"
      f"{int(towns['Institutions'].sum())} 家長照機構，與原始檔一致")
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@500;700;900&family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  /* ─────────────────────────────────────────────────────────────
     視覺語言：資料新聞／研究報告
     紙感米白底、宋體大標、細線分隔、單一磚紅強調色、大量留白。
     刻意不使用圓角卡片與陰影 —— 這是一份「有觀點的分析報告」，
     不是後台管理系統（那是另一個作品的語彙）。
     ───────────────────────────────────────────────────────────── */
  :root{
    --paper:#f6f2ea;      /* 紙張底色 */
    --surface:#fffdf8;    /* 內容區 */
    --ink:#1c1a17;        /* 主文字 */
    --ink-2:#57514a;      /* 次要文字 */
    --ink-3:#8c857c;      /* 註解 */
    --rule:#ded5c6;       /* 分隔線 */
    --rule-2:#ece5d8;
    --accent:#a8391f;     /* 磚紅：唯一的強調色 */
    --accent-soft:#f4e6e0;
    --serif:"Noto Serif TC",Georgia,"Songti TC",serif;
    --sans:"Noto Sans TC",-apple-system,"PingFang TC",sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-weight:400;line-height:1.6;
       -webkit-font-smoothing:antialiased}

  /* 報頭 */
  .header{max-width:1180px;margin:0 auto;padding:44px 28px 22px;border-bottom:3px double var(--rule)}
  .kicker{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);
          font-weight:700;margin-bottom:12px}
  .header h1{font-family:var(--serif);font-weight:900;font-size:40px;line-height:1.2;letter-spacing:.01em}
  .header .src{font-size:13px;color:var(--ink-3);margin-top:12px;font-weight:300}

  /* 章節切換：做成報導的「篇章」，不是分頁標籤 */
  .modules{max-width:1180px;margin:0 auto;padding:0 28px;display:flex;gap:34px;
           border-bottom:1px solid var(--rule);overflow-x:auto}
  .mod{padding:16px 0;cursor:pointer;font-family:var(--serif);font-size:17px;font-weight:500;
       color:var(--ink-3);border-bottom:2px solid transparent;white-space:nowrap;margin-bottom:-1px;
       transition:color .15s}
  .mod:hover{color:var(--ink-2)}
  .mod.on{color:var(--ink);border-bottom-color:var(--accent);font-weight:700}
  .mod .n{font-family:var(--sans);font-size:11px;color:var(--accent);font-weight:700;margin-right:7px;
          vertical-align:2px}

  .wrap{padding:30px 28px 40px;max-width:1180px;margin:0 auto}

  /* 導言：整篇報導要回答的問題 */
  .q{font-family:var(--serif);font-size:22px;font-weight:700;line-height:1.55;color:var(--ink);
     border-left:3px solid var(--accent);padding:2px 0 2px 18px;margin-bottom:26px;max-width:760px}

  /* 關鍵數字：報導開頭的 key figures，用細線隔開而非卡片 */
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
         border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);margin-bottom:26px}
  .card{padding:16px 20px 16px 0;border-left:1px solid var(--rule-2)}
  .card:first-child{border-left:none}
  .card:not(:first-child){padding-left:20px}
  .card .num{font-family:var(--serif);font-size:27px;font-weight:700;color:var(--ink);line-height:1.25;
             font-variant-numeric:tabular-nums}
  .card .lbl{font-size:12px;color:var(--ink-3);margin-top:4px;font-weight:300}

  /* 地圖不設固定高度，由 grid stretch 撐滿整列，避免右欄較高時左下留下大片空白 */
  .main{display:grid;grid-template-columns:1fr 336px;gap:28px;align-items:stretch}
  .map-box{background:var(--surface);border:1px solid var(--rule);min-height:600px;position:relative}
  .map-box iframe{position:absolute;inset:0;width:100%;height:100%;border:none}
  .side{display:flex;flex-direction:column;gap:22px}
  .panel{background:var(--surface);border:1px solid var(--rule);padding:18px}
  .panel h3{font-family:var(--serif);font-size:16px;font-weight:700;color:var(--ink);
            padding-bottom:9px;margin-bottom:11px;border-bottom:1px solid var(--rule)}

  .town{font-family:var(--serif);font-size:24px;font-weight:900;margin-bottom:9px;letter-spacing:.01em}
  .verdict{font-size:14px;font-weight:700;margin-bottom:12px;padding:8px 11px;
           background:var(--accent-soft);color:var(--accent);border-radius:2px}
  .row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px dotted var(--rule);font-size:13px}
  .row span:first-child{color:var(--ink-3);font-weight:300}
  .row span:last-child{font-weight:700;font-variant-numeric:tabular-nums}
  .ph{color:var(--ink-3);font-size:13px;text-align:center;padding:30px 6px;font-weight:300;line-height:1.9}

  /* 評估過程：像報導裡的「編按／方法說明」 */
  .why{margin-top:14px;padding:14px;background:#f2ece0;border-top:2px solid var(--accent);
       font-size:12.5px;line-height:1.85;color:var(--ink-2);font-variant-numeric:tabular-nums}
  .why-title{font-family:var(--serif);font-weight:700;color:var(--ink);margin-bottom:9px;font-size:13.5px}
  .why b{color:var(--ink)}

  .rank-row{display:flex;justify-content:space-between;padding:6px 0;font-size:13.5px;
            border-bottom:1px dotted var(--rule);font-variant-numeric:tabular-nums}
  .rank-row.click{cursor:pointer;padding:7px 8px;margin:0 -8px;transition:background .12s}
  .rank-row.click:hover{background:#f2ece0}
  .rank-row.sel{background:var(--accent-soft);font-weight:700}
  .rank-row .go{color:var(--rule);font-size:11px}
  .rank-row.click:hover .go{color:var(--accent)}
  .tip{font-size:11.5px;color:var(--ink-3);margin-bottom:9px;font-weight:300}

  /* 「往下還有內容」指引 */
  .more{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;
        margin-top:16px;padding:14px 18px;background:var(--surface);border:1px solid var(--rule);
        text-decoration:none;color:var(--ink);font-size:14px;font-family:var(--serif);font-weight:500;
        transition:background .15s}
  .more:hover{background:#f2ece0}
  .more-go{color:var(--accent);font-family:var(--sans);font-weight:700;font-size:12px;
           animation:bob 1.8s ease-in-out infinite}
  @keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(3px)}}
  @media(prefers-reduced-motion:reduce){.more-go{animation:none}}

  /* 方法與限制：報導末尾的編按 */
  .note{margin-top:22px;padding:16px 0;border-top:1px solid var(--rule);
        font-size:12.5px;color:var(--ink-2);line-height:1.9;font-weight:300}
  .note b{color:var(--ink);font-weight:700}

  /* 資料來源與免責聲明 */
  .foot{margin-top:34px;padding-top:26px;border-top:3px double var(--rule);color:var(--ink-2);scroll-margin-top:16px}
  .foot h4{font-family:var(--serif);font-size:19px;font-weight:700;color:var(--ink);margin:0 0 14px}
  .foot h4:not(:first-child){margin-top:34px}
  .src{width:100%;border-collapse:collapse;font-size:12.5px;line-height:1.65}
  .src th{text-align:left;padding:9px 10px 9px 0;color:var(--ink-3);font-size:11px;font-weight:700;
          letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--ink-3)}
  .src td{padding:12px 10px 12px 0;border-bottom:1px solid var(--rule-2);vertical-align:top}
  .src td:first-child{font-weight:700;color:var(--ink)}
  .src small{color:var(--ink-3);font-size:11px;font-weight:300}
  .src a{color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent-soft)}
  .src a:hover{border-bottom-color:var(--accent)}
  .src-wrap{overflow-x:auto}
  .dis{margin:0;padding-left:20px;font-size:13px;line-height:1.95;font-weight:300}
  .dis li{margin-bottom:9px;padding-left:4px}
  .dis li::marker{color:var(--accent);font-weight:700}
  .dis b{color:var(--ink);font-weight:700}
  .lic{margin-top:26px;padding-top:16px;border-top:1px solid var(--rule);font-size:12px;
       color:var(--ink-3);line-height:1.9;font-weight:300}
  .lic a{color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent-soft)}

  .hide{display:none}

  @media(max-width:820px){
    .header{padding:28px 18px 18px}
    .header h1{font-size:27px}
    .modules{padding:0 18px;gap:22px}
    .mod{font-size:15px}
    .wrap{padding:22px 18px 30px}
    .q{font-size:18px}
    .main{grid-template-columns:1fr;gap:20px}
    .map-box{min-height:0;height:380px;order:1}
    .side{order:2}
    .cards{grid-template-columns:1fr 1fr}
    .card{padding:14px 12px !important;border-left:1px solid var(--rule-2);border-top:1px solid var(--rule-2)}
    .card:nth-child(-n+2){border-top:none}
    .card:nth-child(odd){border-left:none;padding-left:0 !important}
    .card .num{font-size:21px}
    .src thead{display:none}
    .src tr{display:block;border-bottom:1px solid var(--rule);padding:10px 0}
    .src td{display:block;border:none;padding:2px 0}
  }
</style>
</head>
<body>
  <div class="header">
    <div class="kicker">Open Data · Spatial Analysis</div>
    <h1>台灣空間決策分析平台</h1>
    <div class="src">以政府開放資料回答兩個空間決策問題　｜　資料來源：內政部戶政司 · 國土測繪中心 · 衛福部食藥署 · 衛福部社家署</div>
  </div>

  <div class="modules">
    <div class="mod on" data-m="site" onclick="switchModule('site')"><span class="n">01</span>餐飲業選址分析</div>
    <div class="mod" data-m="ltc" onclick="switchModule('ltc')"><span class="n">02</span>長照資源缺口分析</div>
  </div>

  <div class="wrap">
    <!-- 模組一：餐飲選址 -->
    <div id="m-site">
      <div class="q">開一家餐飲店，全台哪個鄉鎮的市場最未飽和？</div>
      <div class="cards">__SITE_CARDS__</div>
      <div class="main">
        <div class="map-box"><iframe id="siteFrame" src="site_map.html" title="餐飲業選址分析地圖"></iframe></div>
        <div class="side">
          <div class="panel" id="siteDetail">
            <h3>鄉鎮評估</h3>
            <div class="ph">點選地圖上的鄉鎮，<br>或點下方排行榜的名稱，<br>即可查看評估過程</div>
          </div>
          <div class="panel"><h3>最推薦開店　<span style="color:var(--accent);font-size:12px;font-family:var(--sans)">藍海</span></h3><div class="tip">點任一鄉鎮名稱，查看它的完整評估過程</div>__SITE_TOP__</div>
          <div class="panel"><h3>最不推薦　<span style="color:var(--ink-3);font-size:12px;font-family:var(--sans)">紅海</span></h3><div class="tip">點任一鄉鎮名稱，查看它的完整評估過程</div>__SITE_BOTTOM__</div>
        </div>
      </div>
      <div class="note">
        <b>分析模型：</b>機會指數 = 競爭分數(60%) + 成長分數(40%)。競爭分數看「每店服務人口」（越多代表店少人多、市場未飽和）；成長分數看人口淨變化。僅評估人口 ≥ 2 萬的鄉鎮。<br>
        <b>已知限制：</b>本模型以「居住人口」為市場基礎，但商業區（如臺中中區）有大量通勤消費人口，實際商機會被低估；郊區則可能因外食習慣較低而被高估。實務選址仍需搭配人流、租金與交通資料。
        <a class="more" href="#sources">
        <span>本平台所有數字的資料來源與免責聲明</span>
        <span class="more-go">向下查看 ↓</span>
      </a>
    </div>

    <!-- 模組二：長照缺口 -->
    <div id="m-ltc" class="hide">
      <div class="q">哪些地區的長照資源最不足，應優先增設機構？</div>
      <div class="cards">__LTC_CARDS__</div>
      <div class="main">
        <div class="map-box"><iframe id="ltcFrame" src="ltc_map.html" title="長照資源缺口分析地圖"></iframe></div>
        <div class="side">
          <div class="panel" id="ltcDetail">
            <h3>鄉鎮評估</h3>
            <div class="ph">點選地圖上的鄉鎮，<br>或點下方排行榜的名稱，<br>即可查看評估過程</div>
          </div>
          <div class="panel"><h3>最缺床位　<span style="color:var(--accent);font-size:12px;font-family:var(--sans)">優先增設</span></h3><div class="tip">點任一鄉鎮名稱，查看它的完整評估過程</div>__LTC_TOP__</div>
          <div class="panel"><h3>資源最充足</h3><div class="tip">點任一鄉鎮名稱，查看它的完整評估過程</div>__LTC_BOTTOM__</div>
        </div>
      </div>
      <div class="note">
        <b>分析模型：</b>以「每千名老人床位數」衡量長照供給密度，全國平均為 __BP1K__ 床。缺口指數 0 = 達全國平均，100 = 完全無床位。「達標尚缺床位」= 該鄉鎮達到全國平均水準所需增設的床數。僅評估老年人口 ≥ 3,000 的鄉鎮。<br>
        <b>資料範圍限制（重要）：</b>本資料為衛福部社家署「老人福利機構」名冊，屬<b>住宿式安養／養護機構</b>；<b>不含</b>護理之家（另依《護理機構法》管理）與長照2.0 的居家式／社區式服務（如日照中心）。因此「0 床」代表該鄉鎮無住宿式老人福利機構，<b>不等於完全沒有長照資源</b>。另，年齡結構資料最新僅至 2024 年。
        <a class="more" href="#sources">
        <span>本平台所有數字的資料來源與免責聲明</span>
        <span class="more-go">向下查看 ↓</span>
      </a>
    </div>

    <!-- ── 資料來源與免責聲明 ── -->
    <footer class="foot" id="sources">
      <h4>資料來源</h4>
      <p style="font-size:12.5px;color:var(--ink-3);margin:-8px 0 14px;font-weight:300">以下皆為政府公開資料，非估算或推測值。點擊可直接前往原始資料集。</p>
      <div class="src-wrap">
      <table class="src">
        <thead><tr><th>資料項目</th><th>提供機關</th><th>資料集／來源連結</th><th>本專案採用版本</th></tr></thead>
        <tbody>
          <tr>
            <td>鄉鎮人口數<br><small>（人口、人口淨變化）</small></td>
            <td>內政部戶政司</td>
            <td><a href="https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP019/114" target="_blank" rel="noopener">戶政司開放資料 API — ODRP019<br><small>鄉鎮市區人口數（按性別及年齡）</small></a></td>
            <td>民國 114 年（2025）<br><small>人口淨變化 = 114 年 − 113 年</small></td>
          </tr>
          <tr>
            <td>65 歲以上老年人口</td>
            <td>內政部戶政司</td>
            <td><a href="https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP052/113" target="_blank" rel="noopener">戶政司開放資料 API — ODRP052<br><small>現住人口按性別、年齡、婚姻狀況</small></a></td>
            <td>民國 113 年（2024）<br><small>此資料集最新僅至此年度</small></td>
          </tr>
          <tr>
            <td>餐飲場所家數<br><small>（__N_REST__）</small></td>
            <td>衛生福利部<br>食品藥物管理署</td>
            <td><a href="https://data.gov.tw/dataset/8938" target="_blank" rel="noopener">政府資料開放平臺 — 資料集 8938<br><small>食品業者登錄資料集</small></a></td>
            <td>篩選「登錄項目＝餐飲場所」</td>
          </tr>
          <tr>
            <td>老人福利機構與床位<br><small>（__N_LTC__）</small></td>
            <td>衛生福利部<br>社會及家庭署</td>
            <td><a href="https://data.gov.tw/dataset/8572" target="_blank" rel="noopener">政府資料開放平臺 — 資料集 8572<br><small>全國老人福利機構名冊</small></a></td>
            <td>22 縣市名冊彙整</td>
          </tr>
          <tr>
            <td>鄉鎮市區界線圖資<br><small>（__N_TOWN__ 個鄉鎮）</small></td>
            <td>內政部國土測繪中心</td>
            <td><a href="https://data.gov.tw/dataset/7441" target="_blank" rel="noopener">政府資料開放平臺 — 資料集 7441<br><small>鄉鎮市區界線（TWD97 經緯度）</small></a></td>
            <td>TWD97 / EPSG:3826<br><small>已套用行政區改制對照<br>（桃園升格等）</small></td>
          </tr>
        </tbody>
      </table>
      </div>

      <h4>免責聲明</h4>
      <ol class="dis">
        <li><b>指標為本人自行建立的分析模型，非政府官方指標。</b>「選址機會指數」與「長照缺口指數」的權重（60/40）與門檻（人口 ≥ 2 萬、老年人口 ≥ 3,000）由我依分析目的設定，政府並未發布此類指數。原始統計數字（人口、店家數、床位數）則完全取自上表來源，未經任何調整或推估。</li>
          <li><b>資料時點不一致。</b>人口為民國 114 年（2025）、年齡結構為民國 113 年（2024，該資料集最新僅至此年度）、餐飲與長照名冊為下載當日之最新版。跨年度比較時請注意此落差。</li>
        <li><b>餐飲場所家數為「已登錄業者」數，非實際營業家數。</b>依《食品安全衛生管理法》第 8 條，食品業者應辦理登錄；但登錄後歇業未註銷者仍會計入，小規模攤商亦可能未登錄，故數字會與街上實際店數有出入。</li>
        <li><b>長照資料僅涵蓋住宿式老人福利機構。</b>不含護理之家（另依《護理機構分類設置標準》管理）與長照 2.0 之居家式／社區式服務（如日間照顧中心）。因此「0 床」代表該鄉鎮無住宿式老人福利機構，<b>不代表該地完全沒有長照資源</b>。</li>
        <li><b>選址模型以「居住人口」為市場基礎。</b>商業區（如臺中中區）有大量通勤消費人口，實際商機會被低估；郊區則可能因外食習慣較低而被高估。實務選址仍須搭配人流、租金與交通資料。</li>
        <li>本平臺為個人技術作品，分析結果<b>僅供技術能力展示，不構成任何投資、開店或政策建議</b>。</li>
      </ol>

      <p class="lic">
        政府資料依「<a href="https://data.gov.tw/license" target="_blank" rel="noopener">政府資料開放授權條款－第 1 版</a>」使用。
        分析程式碼（ETL、指標計算、地圖產生）為本人自行撰寫，
        原始碼公開於 <a href="https://github.com/Hzin-Tsu-Chen/taiwan-population-map" target="_blank" rel="noopener">GitHub</a>，可完整檢視每一個數字的計算過程。
      </p>
    </footer>
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

    // Leaflet 在 display:none 的容器裡初始化時，量到的尺寸是 0×0，
    // 容器顯示出來後它並不知道自己變大了 —— 結果就是一片灰白。
    // 對 iframe 觸發一次 resize，Leaflet 會自行重算容器尺寸並重繪。
    var frame = document.getElementById(m === 'site' ? 'siteFrame' : 'ltcFrame');
    requestAnimationFrame(function() {
      try { frame.contentWindow.dispatchEvent(new Event('resize')); } catch (e) {}
    });
  }

  // 顯示某鄉鎮的評估 —— 地圖點選與排行榜點選共用此函式
  function showTown(n) {
    if (!n || !D[n]) return;
    var d = D[n];

    document.getElementById('siteDetail').innerHTML =
      '<h3>鄉鎮評估</h3><div class="town">' + n + '</div>' +
      '<div class="verdict">' + d.SiteVerdict + '</div>' +
      '<div class="row"><span>機會指數</span><span>' + d.Opportunity + '</span></div>' +
      '<div class="row"><span>人口</span><span>' + d.Population + '</span></div>' +
      '<div class="row"><span>現有餐飲店</span><span>' + d.Restaurants + ' 家</span></div>' +
      '<div class="row"><span>每店服務人口</span><span>' + d.PeoplePerStore + ' 人</span></div>' +
      '<div class="why"><div class="why-title">評估過程（實際計算，可逐項驗證）</div>' + d.SiteWhy + '</div>';

    document.getElementById('ltcDetail').innerHTML =
      '<h3>鄉鎮評估</h3><div class="town">' + n + '</div>' +
      '<div class="verdict">' + d.LtcVerdict + '</div>' +
      '<div class="row"><span>65歲以上人口</span><span>' + d.Elderly65 + '</span></div>' +
      '<div class="row"><span>長照機構</span><span>' + d.Institutions + '</span></div>' +
      '<div class="row"><span>核定床位</span><span>' + d.Beds + '</span></div>' +
      '<div class="row"><span>每千名老人床位</span><span>' + d.BedsPer1000 + '</span></div>' +
      '<div class="row"><span>達標尚缺床位</span><span>' + d.BedShortfall + '</span></div>' +
      '<div class="why"><div class="why-title">評估過程（實際計算，可逐項驗證）</div>' + d.LtcWhy + '</div>';

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
        .replace('__N_TOWN__', str(len(towns)))
        .replace('__N_REST__', f"{int(towns['Restaurants'].sum()):,} 家登錄")
        .replace('__N_LTC__', f"{int(towns['Institutions'].sum()):,} 家、{int(towns['Beds'].sum()):,} 床")
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
