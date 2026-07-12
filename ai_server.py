"""
GeoAI 問答後端：讓使用者用自然語言詢問台灣空間資料，由 LLM 即時分析回答

安全設計：
  - API 金鑰從「環境變數」讀取，絕不寫死在程式碼（也因此不會被推上 GitHub）
  - 限流：每個 IP 每小時最多 10 題，避免公開端點被濫用
  - 每日總量上限，控制成本
  - 系統提示限制 AI 只回答與本資料相關的問題

執行：
  export ANTHROPIC_API_KEY=your-key   # 由使用者自行設定
  python3 ai_server.py
"""

import os
import csv
import json
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic

# ── 安全設定 ──
API_KEY = os.environ.get('ANTHROPIC_API_KEY')     # 只從環境變數讀
RATE_LIMIT_PER_HOUR = 10                          # 每 IP 每小時上限
DAILY_TOTAL_LIMIT = 200                           # 全站每日總量上限（成本保護）
MODEL = 'claude-haiku-4-5'                        # 最便宜的模型，做 demo 足夠

if not API_KEY:
    raise SystemExit('請先設定環境變數 ANTHROPIC_API_KEY')

client = anthropic.Anthropic(api_key=API_KEY)

# ── 載入資料，組成給 AI 的背景知識 ──
def load_context():
    rows = list(csv.DictReader(open('town_indicators.csv', encoding='utf-8')))
    lines = ['縣市,鄉鎮,人口,餐飲店數,每店服務人口,人口淨變化']
    for r in rows:
        lines.append(f"{r['COUNTYNAME']},{r['TOWNNAME']},{r['Population']},"
                     f"{r['Restaurants']},{r['PeoplePerStore']},{r['PopChange']}")
    return '\n'.join(lines)


DATA_CONTEXT = load_context()

SYSTEM_PROMPT = f"""你是台灣空間資料分析助理。以下是全台 367 個鄉鎮的資料（2025 年，來源：內政部戶政司 + 衛福部食藥署）：

{DATA_CONTEXT}

規則：
1. 只根據上述資料回答，不要編造數字。
2. 只回答與這份台灣鄉鎮資料相關的問題；無關問題請婉拒並說明你的用途。
3. 回答要具體、附上數字，並在適當時給出決策建議。
4. 用繁體中文，簡潔清楚（150 字內為佳）。
5. 「每店服務人口」越高代表餐飲市場越未飽和；全國平均約 102 人。
"""

# ── 限流狀態（記憶體，重啟歸零；正式環境應改用 Redis）──
_ip_hits = defaultdict(list)
_daily_count = {'date': time.strftime('%Y-%m-%d'), 'count': 0}


def check_limits(ip):
    """回傳 None 代表通過；否則回傳拒絕原因"""
    today = time.strftime('%Y-%m-%d')
    if _daily_count['date'] != today:
        _daily_count.update(date=today, count=0)
    if _daily_count['count'] >= DAILY_TOTAL_LIMIT:
        return '今日提問額度已用完，請明天再試。'

    now = time.time()
    _ip_hits[ip] = [t for t in _ip_hits[ip] if now - t < 3600]   # 只留一小時內
    if len(_ip_hits[ip]) >= RATE_LIMIT_PER_HOUR:
        return f'提問過於頻繁，每小時上限 {RATE_LIMIT_PER_HOUR} 題，請稍後再試。'
    return None


def ask_ai(question):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=[{
            'type': 'text',
            'text': SYSTEM_PROMPT,
            'cache_control': {'type': 'ephemeral'},   # 快取資料前綴，大幅降低成本
        }],
        messages=[{'role': 'user', 'content': question}],
    )
    return ''.join(b.text for b in resp.content if b.type == 'text')


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {})

    def do_POST(self):
        if self.path != '/ask':
            return self._send(404, {'error': 'not found'})

        ip = self.headers.get('X-Forwarded-For', self.client_address[0]).split(',')[0].strip()
        blocked = check_limits(ip)
        if blocked:
            return self._send(429, {'error': blocked})

        try:
            length = int(self.headers.get('Content-Length', 0))
            question = json.loads(self.rfile.read(length)).get('question', '').strip()
        except Exception:
            return self._send(400, {'error': '請求格式錯誤'})

        if not question or len(question) > 300:
            return self._send(400, {'error': '問題不可為空，且長度需在 300 字以內'})

        _ip_hits[ip].append(time.time())
        _daily_count['count'] += 1

        try:
            answer = ask_ai(question)
            return self._send(200, {'answer': answer})
        except Exception as e:
            print('AI 呼叫失敗:', e)
            return self._send(500, {'error': 'AI 服務暫時無法使用'})

    def log_message(self, *args):
        pass   # 關閉預設的請求日誌


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f'GeoAI 問答服務啟動於 http://localhost:{port}')
    print(f'模型：{MODEL}｜每IP每小時 {RATE_LIMIT_PER_HOUR} 題｜每日上限 {DAILY_TOTAL_LIMIT} 題')
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
