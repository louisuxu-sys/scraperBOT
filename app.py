"""
SPORTIQ LINE Bot
體育即時比分 · AI 智能分析 LINE Bot

使用方式：
  傳送「籃球」「棒球」「足球」「冰球」「網球」查看今日賽事
  傳送「分析 隊名」查看該隊比賽的 AI 分析
  傳送「比分」查看今日所有比分
  傳送「明天 籃球」查看明天籃球賽事
  傳送「昨天 棒球」查看昨天棒球賽事
"""
import os
import re
from datetime import datetime, timedelta, timezone

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

from scraper import fetch_all_games, PS_LEAGUES
from analyzer import (
    format_all_games_text,
    format_analysis_text,
    format_game_text,
    generate_analysis,
)

# ===== 設定 =====
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    print('⚠️  請設定環境變數 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET')
    print('   export LINE_CHANNEL_ACCESS_TOKEN="你的 Channel Access Token"')
    print('   export LINE_CHANNEL_SECRET="你的 Channel Secret"')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 台灣時區
TW_TZ = timezone(timedelta(hours=8))

# 運動關鍵字對照
SPORT_KEYWORDS = {
    '籃球': 'basketball', 'nba': 'basketball', 'sbl': 'basketball',
    '棒球': 'baseball', 'mlb': 'baseball', '中職': 'baseball', '日職': 'baseball',
    '足球': 'soccer', '冰球': 'hockey', 'nhl': 'hockey',
    '網球': 'tennis',
}

# 快取（避免頻繁爬取）
_cache = {}
CACHE_TTL = 120  # 秒


def get_games_cached(sport, gamedate):
    """帶快取的資料取得"""
    key = f'{sport}_{gamedate}'
    now = datetime.now(TW_TZ).timestamp()
    if key in _cache and now - _cache[key]['time'] < CACHE_TTL:
        return _cache[key]['data']

    games = fetch_all_games(sport, gamedate)
    _cache[key] = {'data': games, 'time': now}
    return games


def parse_user_message(text):
    """
    解析使用者訊息，回傳 (action, sport, date_offset, keyword)
    action: 'list' | 'analysis' | 'help'
    """
    text = text.strip().lower()

    # 幫助
    if text in ('help', '幫助', '說明', '指令', '功能', '選單', 'menu'):
        return 'help', None, 0, None

    # 日期偏移
    date_offset = 0
    if '昨天' in text or '昨日' in text:
        date_offset = -1
        text = text.replace('昨天', '').replace('昨日', '').strip()
    elif '明天' in text or '明日' in text:
        date_offset = 1
        text = text.replace('明天', '').replace('明日', '').strip()
    elif '後天' in text:
        date_offset = 2
        text = text.replace('後天', '').strip()

    # 分析指令
    if text.startswith('分析'):
        keyword = text[2:].strip()
        return 'analysis', None, date_offset, keyword

    # 比分指令
    if text in ('比分', '即時比分', 'score', 'scores', '今日比分'):
        return 'list', 'basketball', date_offset, None

    # 運動類型
    for kw, sport in SPORT_KEYWORDS.items():
        if kw in text:
            # 檢查是否有分析需求
            if '分析' in text:
                keyword = text.replace(kw, '').replace('分析', '').strip()
                return 'analysis', sport, date_offset, keyword or None
            return 'list', sport, date_offset, None

    # 預設：如果是簡短文字，可能是隊名搜尋
    if len(text) <= 10 and text not in ('', ' '):
        return 'analysis', None, date_offset, text

    return 'help', None, 0, None


def get_date_str(offset=0):
    """取得日期字串 YYYYMMDD"""
    now = datetime.now(TW_TZ)
    target = now + timedelta(days=offset)
    return target.strftime('%Y%m%d')


def get_display_date(offset=0):
    """取得顯示用日期"""
    now = datetime.now(TW_TZ)
    target = now + timedelta(days=offset)
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    return f'{target.month}/{target.day} ({weekdays[target.weekday()]})'


def build_help_message():
    """建立說明訊息"""
    return (
        '🏆 SPORTIQ 體育分析 Bot\n'
        '━━━━━━━━━━━━━━━\n'
        '\n'
        '📌 查看賽事：\n'
        '  👉 傳送「籃球」「棒球」「足球」「冰球」「網球」\n'
        '\n'
        '📌 查看 AI 分析：\n'
        '  👉 傳送「分析 隊名」\n'
        '  例：分析 湖人\n'
        '  例：分析 勇士\n'
        '\n'
        '📌 查看不同日期：\n'
        '  👉 傳送「昨天 籃球」\n'
        '  👉 傳送「明天 棒球」\n'
        '\n'
        '📌 快速指令：\n'
        '  👉「比分」→ 今日籃球比分\n'
        '  👉「NBA」→ NBA 賽事\n'
        '  👉「MLB」→ MLB 賽事\n'
        '\n'
        '資料來源：playsport.cc'
    )


def find_game_by_keyword(games, keyword):
    """根據關鍵字找到匹配的比賽"""
    if not keyword:
        return []

    keyword = keyword.lower()
    matched = []
    for g in games:
        home = g.get('home', '').lower()
        away = g.get('away', '').lower()
        if keyword in home or keyword in away:
            matched.append(g)

    return matched


def handle_list(sport, date_offset):
    """處理賽事列表請求"""
    gamedate = get_date_str(date_offset)
    display_date = get_display_date(date_offset)
    games = get_games_cached(sport, gamedate)

    if not games:
        sport_name = {'basketball': '籃球', 'baseball': '棒球', 'soccer': '足球',
                      'hockey': '冰球', 'tennis': '網球'}.get(sport, sport)
        return f'📅 {display_date}\n\n{sport_name} 今日無賽事，請切換日期或運動類型。'

    return format_all_games_text(games, sport, display_date)


def handle_analysis(sport, date_offset, keyword):
    """處理分析請求"""
    # 如果沒指定運動，搜尋所有運動
    sports_to_search = [sport] if sport else ['basketball', 'baseball', 'soccer', 'hockey', 'tennis']

    gamedate = get_date_str(date_offset)
    all_matched = []

    for s in sports_to_search:
        games = get_games_cached(s, gamedate)
        if keyword:
            matched = find_game_by_keyword(games, keyword)
            for g in matched:
                all_matched.append((g, s))
        elif games:
            # 沒有關鍵字，分析第一場
            all_matched.append((games[0], s))
            break

    if not all_matched:
        if keyword:
            return f'❌ 找不到與「{keyword}」相關的賽事。\n\n請確認隊名是否正確，或嘗試其他關鍵字。'
        return '❌ 今日暫無賽事資料。'

    # 回傳每場匹配比賽的分析
    results = []
    for game, s in all_matched[:3]:  # 最多 3 場
        text = format_analysis_text(game, s)
        results.append(text)

    return '\n\n'.join(results)


# ===== Flask Routes =====

@app.route('/callback', methods=['POST'])
def callback():
    """LINE Webhook callback"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'


@app.route('/health', methods=['GET'])
def health():
    """健康檢查"""
    return {'status': 'ok', 'service': 'sportiq-linebot'}


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """處理使用者訊息"""
    text = event.message.text.strip()
    action, sport, date_offset, keyword = parse_user_message(text)

    if action == 'help':
        reply = build_help_message()
    elif action == 'list':
        reply = handle_list(sport or 'basketball', date_offset)
    elif action == 'analysis':
        reply = handle_analysis(sport, date_offset, keyword)
    else:
        reply = build_help_message()

    # LINE 訊息長度限制 5000 字
    if len(reply) > 5000:
        reply = reply[:4950] + '\n\n... (訊息過長，已截斷)'

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )


# ===== 啟動 =====
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'========================================')
    print(f'  SPORTIQ LINE Bot')
    print(f'  http://localhost:{port}')
    print(f'========================================')
    print(f'  Webhook URL: https://你的網域/callback')
    print(f'  Health:      http://localhost:{port}/health')
    print(f'========================================')
    app.run(host='0.0.0.0', port=port, debug=False)
