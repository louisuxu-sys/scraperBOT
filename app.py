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
    QuickReply,
    QuickReplyItem,
    MessageAction,
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
from membership import (
    is_admin, add_admin, remove_admin,
    generate_code, redeem_code,
    is_member_active, get_member_expiry,
    DURATION_OPTIONS,
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

# 運動類型顯示設定
SPORT_OPTIONS = [
    {'key': 'basketball', 'name': '籃球', 'emoji': '🏀'},
    {'key': 'baseball',   'name': '棒球', 'emoji': '⚾'},
    {'key': 'soccer',     'name': '足球', 'emoji': '⚽'},
    {'key': 'hockey',     'name': '冰球', 'emoji': '🏒'},
    {'key': 'tennis',     'name': '網球', 'emoji': '🎾'},
]

# 快取（避免頻繁爬取）
_cache = {}
CACHE_TTL = 120  # 秒

# 用戶 session：記住每個用戶目前瀏覽的日期偏移
_user_date_offset = {}  # uid -> date_offset


def get_games_cached(sport, gamedate):
    """帶快取的資料取得"""
    key = f'{sport}_{gamedate}'
    now = datetime.now(TW_TZ).timestamp()
    if key in _cache and now - _cache[key]['time'] < CACHE_TTL:
        return _cache[key]['data']

    games = fetch_all_games(sport, gamedate)
    _cache[key] = {'data': games, 'time': now}
    return games


def parse_user_message(raw_text):
    """
    解析使用者訊息，回傳 (action, sport, date_offset, keyword)
    action: 'list' | 'analysis' | 'help' | 'query_uid' | 'set_admin' | 'gen_code' ...
    """
    raw = raw_text.strip()
    text = raw.lower()

    # 隱藏指令：查詢UID
    if text in ('查詢uid', 'uid', '我的uid'):
        return 'query_uid', None, 0, None

    # 管理員指令：設為管理員 <uid>
    if text.startswith('設為管理員'):
        target_uid = raw[5:].strip()  # 保留原始大小寫
        return 'set_admin', None, 0, target_uid or None

    # 管理員指令：移除管理員 <uid>
    if text.startswith('移除管理員'):
        target_uid = raw[5:].strip()
        return 'remove_admin', None, 0, target_uid or None

    # 管理員指令：生成序號 <期限>
    if text.startswith('生成序號'):
        duration = raw[4:].strip()
        return 'gen_code', None, 0, duration or None

    # 幫助
    if text in ('help', '幫助', '說明', '指令', '功能', 'menu'):
        return 'help', None, 0, None

    # 查詢到期
    if text in ('查詢到期', '到期', '到期日', '會員到期'):
        return 'check_expiry', None, 0, None

    # 儲值序號
    if text.startswith('儲值序號') or text == '儲值':
        code = raw.replace('儲值序號', '').replace('儲值', '').strip()
        return 'redeem', None, 0, code or None

    # 主選單
    if text in ('主選單', '選單', '返回', '返回主選單'):
        return 'main_menu', None, 0, None

    # 今日賽事 / 明日賽事：觸發運動選單
    if text in ('今日賽事', '賽事', '今天'):
        return 'select_sport', None, 0, None
    if text in ('明日賽事',):
        return 'select_sport', None, 1, None

    # 返回運動選擇
    if text in ('返回運動選擇', '選運動'):
        return 'select_sport', None, 0, None

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
        return 'select_sport', None, 0, None

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
        '╭────────────────╮\n'
        '│  🏆 SPORTIQ              │\n'
        '│  體育即時分析平台     │\n'
        '╰────────────────╯\n'
        '\n'
        '▸ 查看賽事\n'
        '  點擊「🏆 今日賽事」按鈕\n'
        '  或輸入「籃球」「棒球」「足球」...\n'
        '\n'
        '▸ AI 智能分析\n'
        '  在賽事列表中點擊比賽按鈕\n'
        '  或輸入「分析 隊名」\n'
        '\n'
        '▸ 查看不同日期\n'
        '  點擊「� 明日賽事」按鈕\n'
        '  或輸入「明天 籃球」\n'
        '\n'
        '──────────────────\n'
        '📡 資料來源：playsport.cc'
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
    """處理賽事列表請求，回傳 (text, games)"""
    gamedate = get_date_str(date_offset)
    display_date = get_display_date(date_offset)
    games = get_games_cached(sport, gamedate)

    if not games:
        sport_name = {'basketball': '籃球', 'baseball': '棒球', 'soccer': '足球',
                      'hockey': '冰球', 'tennis': '網球'}.get(sport, sport)
        return f'📅 {display_date}\n\n{sport_name} 今日無賽事，請切換日期或運動類型。', []

    return format_all_games_text(games, sport, display_date), games


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


def handle_check_expiry(user_id):
    """查詢會員到期日"""
    expiry = get_member_expiry(user_id)
    admin_tag = '  👑 管理員' if is_admin(user_id) else ''

    if expiry:
        return (
            f'╭────────────────╮\n'
            f'│  📋 會員狀態{admin_tag}       │\n'
            f'╰────────────────╯\n'
            f'\n'
            f'{expiry}'
        )

    return (
        '╭────────────────╮\n'
        '│  📋 會員狀態            │\n'
        '╰────────────────╯\n'
        '\n'
        '⚠️ 尚未開通會員資格\n\n'
        '▸ 請輸入「儲值序號 你的序號」\n'
        '  來開通或續費會員。'
    )


def handle_redeem(user_id, code):
    """儲值序號"""
    if not code:
        return (
            '╭────────────────╮\n'
            '│  💰 儲值序號            │\n'
            '╰────────────────╯\n'
            '\n'
            '請輸入您的儲值序號：\n\n'
            '▸ 格式\n'
            '  儲值序號 XXXX-XXXX-XXXX\n\n'
            '▸ 範例\n'
            '  儲值序號 AB12-CD34-EF56'
        )

    success, msg = redeem_code(user_id, code)
    icon = '✅' if success else '❌'
    return (
        f'╭────────────────╮\n'
        f'│  {icon} 儲值序號            │\n'
        f'╰────────────────╯\n'
        f'\n{msg}'
    )


def handle_query_uid(user_id):
    """查詢用戶 UID（隱藏指令）"""
    role = '👑 管理員' if is_admin(user_id) else '👤 一般用戶'
    member = get_member_expiry(user_id) or '⚠️ 未開通'
    return (
        f'╭────────────────╮\n'
        f'│  🔑 用戶資訊            │\n'
        f'╰────────────────╯\n'
        f'\n'
        f'▸ 身份：{role}\n'
        f'▸ 會員：{member}\n\n'
        f'──────────────────\n'
        f'UID：\n{user_id}'
    )


def handle_set_admin(operator_uid, target_uid):
    """設為管理員（僅管理員可操作）"""
    if not is_admin(operator_uid):
        return (
            '╭────────────────╮\n'
            '│  ⛔ 權限不足            │\n'
            '╰────────────────╯\n'
            '\n僅管理員可執行此操作。'
        )
    if not target_uid:
        return (
            '╭────────────────╮\n'
            '│  👑 設為管理員         │\n'
            '╰────────────────╯\n'
            '\n請提供目標用戶 UID\n\n'
            '▸ 格式：設為管理員 <UID>'
        )

    added = add_admin(target_uid)
    if added:
        return (
            '╭────────────────╮\n'
            '│  ✅ 操作成功            │\n'
            '╰────────────────╯\n'
            f'\n已將以下用戶設為管理員：\n'
            f'{target_uid[:20]}...'
        )
    return f'⚠️ {target_uid[:10]}... 已經是管理員。'


def handle_remove_admin(operator_uid, target_uid):
    """移除管理員"""
    if not is_admin(operator_uid):
        return (
            '╭────────────────╮\n'
            '│  ⛔ 權限不足            │\n'
            '╰────────────────╯\n'
            '\n僅管理員可執行此操作。'
        )
    if not target_uid:
        return '❌ 請提供目標用戶 UID。\n\n▸ 格式：移除管理員 <UID>'

    removed = remove_admin(target_uid)
    if removed:
        return (
            '╭────────────────╮\n'
            '│  ✅ 操作成功            │\n'
            '╰────────────────╯\n'
            f'\n已移除以下用戶的管理員資格：\n'
            f'{target_uid[:20]}...'
        )
    return f'⚠️ {target_uid[:10]}... 不是管理員。'


def handle_gen_code(operator_uid, duration_label):
    """生成序號（僅管理員）"""
    if not is_admin(operator_uid):
        return (
            '╭────────────────╮\n'
            '│  ⛔ 權限不足            │\n'
            '╰────────────────╯\n'
            '\n僅管理員可執行此操作。'
        )

    if not duration_label:
        options = '\n'.join([f'  ▸ {k}' for k in DURATION_OPTIONS.keys()])
        return (
            '╭────────────────╮\n'
            '│  🎫 生成序號            │\n'
            '╰────────────────╯\n'
            '\n'
            '請指定有效期限：\n'
            f'{options}\n\n'
            '──────────────────\n'
            '範例：生成序號 7天'
        )

    code, duration_min = generate_code(operator_uid, duration_label)
    if not code:
        options = '、'.join(DURATION_OPTIONS.keys())
        return f'❌ 無效的期限。\n\n可用選項：{options}'

    return (
        '╭────────────────╮\n'
        '│  ✅ 序號生成成功       │\n'
        '╰────────────────╯\n'
        f'\n'
        f'▸ 序號\n'
        f'  {code}\n\n'
        f'▸ 有效期限\n'
        f'  {duration_label}\n\n'
        f'──────────────────\n'
        f'用戶輸入以下內容即可開通：\n'
        f'儲值序號 {code}'
    )


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
    return {'status': 'ok', 'service': 'sportiq-linebot', 'version': 'v2.1'}


# ===== Quick Reply 階層選單 =====

def build_main_menu_qr():
    """第一層：主選單"""
    return [
        QuickReplyItem(action=MessageAction(label='🏆 今日賽事', text='今日賽事')),
        QuickReplyItem(action=MessageAction(label='📅 明日賽事', text='明日賽事')),
        QuickReplyItem(action=MessageAction(label='🔍 查詢到期', text='查詢到期')),
        QuickReplyItem(action=MessageAction(label='💰 儲值序號', text='儲值序號')),
    ]


def build_sport_select_qr(date_offset=0):
    """第二層：選運動類型"""
    prefix = '' if date_offset == 0 else '明天 '
    items = []
    for s in SPORT_OPTIONS:
        label = f'{s["emoji"]} {s["name"]}'
        cmd = f'{prefix}{s["name"]}' if prefix else s['name']
        items.append(QuickReplyItem(action=MessageAction(label=label, text=cmd.strip())))
    items.append(
        QuickReplyItem(action=MessageAction(label='↩ 返回主選單', text='返回主選單'))
    )
    return items


def build_game_qr(game_list, sport_name=''):
    """第三層：每場比賽的分析按鈕"""
    game_buttons = []
    seen = set()
    for g in game_list:
        home = g.get('home', '')
        away = g.get('away', '')
        if home and home != '—' and home not in seen:
            # 顯示「客隊 vs 主隊」讓用戶清楚是哪場比賽
            vs_text = f'{away}v{home}' if away and away != '—' else home
            label = f'📊 {vs_text[:10]}' if len(vs_text) > 10 else f'📊 {vs_text}'
            game_buttons.append(
                QuickReplyItem(action=MessageAction(label=label, text=f'分析 {home}'))
            )
            seen.add(home)
        if len(game_buttons) >= 11:  # 留 2 個給返回按鈕
            break
    game_buttons.append(
        QuickReplyItem(action=MessageAction(label='↩ 返回運動選擇', text='返回運動選擇'))
    )
    game_buttons.append(
        QuickReplyItem(action=MessageAction(label='🏠 主選單', text='返回主選單'))
    )
    return game_buttons


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """處理使用者訊息"""
    text = event.message.text.strip()
    uid = event.source.user_id
    action, sport, date_offset, keyword = parse_user_message(text)

    game_list = []
    qr_items = build_main_menu_qr()  # 預設回到第一層

    # 不需要會員的指令
    if action == 'main_menu':
        reply = (
            '╭────────────────╮\n'
            '│  🏆 SPORTIQ              │\n'
            '│  體育即時分析平台     │\n'
            '╰────────────────╯\n'
            '\n'
            '👇 請點擊下方按鈕選擇功能'
        )
    elif action == 'help':
        reply = build_help_message()
    elif action == 'query_uid':
        reply = handle_query_uid(uid)
    elif action == 'set_admin':
        reply = handle_set_admin(uid, keyword)
    elif action == 'remove_admin':
        reply = handle_remove_admin(uid, keyword)
    elif action == 'gen_code':
        reply = handle_gen_code(uid, keyword)
    elif action == 'check_expiry':
        reply = handle_check_expiry(uid)
    elif action == 'redeem':
        reply = handle_redeem(uid, keyword)

    # 需要會員的指令
    elif action in ('select_sport', 'list', 'analysis'):
        if not is_member_active(uid):
            reply = (
                '╭────────────────╮\n'
                '│  🔒 權限不足            │\n'
                '╰────────────────╯\n'
                '\n'
                '此功能需要會員資格\n\n'
                '▸ 請先儲值序號來開通會員\n'
                '  格式：儲值序號 XXXX-XXXX-XXXX\n\n'
                '▸ 輸入「查詢到期」可查看會員狀態'
            )
        elif action == 'select_sport':
            _user_date_offset[uid] = date_offset
            display_date = get_display_date(date_offset)
            reply = (
                f'╭────────────────╮\n'
                f'│  🏆 選擇運動類型       │\n'
                f'│  📅 {display_date}            │\n'
                f'╰────────────────╯\n'
                f'\n'
                f'👇 點擊下方按鈕選擇想查看的運動'
            )
            qr_items = build_sport_select_qr(date_offset)
        elif action == 'list':
            _user_date_offset[uid] = date_offset
            sport_name = {'basketball': '籃球', 'baseball': '棒球', 'soccer': '足球',
                          'hockey': '冰球', 'tennis': '網球'}.get(sport or '', '')
            reply, game_list = handle_list(sport or 'basketball', date_offset)
            if game_list:
                qr_items = build_game_qr(game_list, sport_name)
            else:
                qr_items = build_sport_select_qr(date_offset)
        elif action == 'analysis':
            # 如果用戶沒有明確指定日期，使用上次瀏覽的日期
            if date_offset == 0 and uid in _user_date_offset:
                date_offset = _user_date_offset[uid]
            reply = handle_analysis(sport, date_offset, keyword)
    else:
        reply = build_help_message()

    # LINE 訊息長度限制 5000 字
    if len(reply) > 5000:
        reply = reply[:4950] + '\n\n... (訊息過長，已截斷)'

    quick_reply = QuickReply(items=qr_items[:13])

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply, quick_reply=quick_reply)]
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
