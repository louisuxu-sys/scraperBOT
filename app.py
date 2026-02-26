"""
新紀元 LINE Bot
體育即時比分 · 智能分析 LINE Bot

使用方式：
  傳送「籃球」「棒球」「足球」「冰球」「網球」查看今日賽事
  傳送「分析 隊名」查看該隊比賽的詳細分析
  傳送「比分」查看今日所有比分
  傳送「明天 籃球」查看明天籃球賽事
  傳送「昨天 棒球」查看昨天棒球賽事
"""
import os
import re
import threading
import urllib.request
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
    format_league_games_text,
    format_analysis_text,
    format_game_text,
    format_yesterday_results,
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

# 用戶 session：記住每個用戶目前瀏覽的日期偏移和運動類型
_user_session = {}  # uid -> {'date_offset': int, 'sport': str}

# 用戶等待輸入序號狀態
_user_waiting_redeem = set()  # uid set


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

    # 管理員指令：產生序號 <期限> <數量>
    if text.startswith('產生序號') or text.startswith('生成序號'):
        params = raw[4:].strip()
        return 'gen_code', None, 0, params or None

    # 幫助
    if text in ('help', '幫助', '說明', '指令', '功能', 'menu'):
        return 'help', None, 0, None

    # 查詢到期（保留指令）
    if text in ('查詢到期', '到期', '到期日', '會員到期'):
        return 'check_expiry', None, 0, None

    # 儲值序號
    if text.startswith('儲值序號'):
        code = raw[4:].strip()
        return 'redeem', None, 0, code or None
    if text == '儲值':
        return 'redeem', None, 0, None

    # 主選單
    if text in ('主選單', '選單', '返回', '返回主選單'):
        return 'main_menu', None, 0, None

    # ===== 四層選單系統 =====
    # 格式：「功能 球類 聯賽」
    MENU_CMDS = {
        '今日賽事': 0, '明日賽事': 1, '昨日賽果': -1, '今日賽果': 0,
    }
    SPORT_NAME_MAP = {v: k for k, v in {
        'basketball': '籃球', 'baseball': '棒球',
        'soccer': '足球', 'hockey': '冰球', 'tennis': '網球'
    }.items()}

    for cmd, offset in MENU_CMDS.items():
        if text == cmd:
            # 第一層 → 第二層：選球類
            action_type = 'results' if '賽果' in cmd else 'events'
            return f'menu_sport_{action_type}', None, offset, None
        if text.startswith(cmd + ' '):
            rest = text[len(cmd)+1:].strip()
            parts = rest.split(' ', 1)
            sport_name = parts[0]
            sport_key = SPORT_NAME_MAP.get(sport_name)
            if sport_key:
                if len(parts) == 1:
                    # 第二層 → 第三層：選聯賽
                    action_type = 'results' if '賽果' in cmd else 'events'
                    return f'menu_league_{action_type}', sport_key, offset, cmd
                else:
                    # 第三層 → 第四層：顯示聯賽賽事
                    league_name = parts[1]
                    action_type = 'results' if '賽果' in cmd else 'events'
                    return f'menu_games_{action_type}', sport_key, offset, league_name

    # 返回運動選擇
    if text in ('返回運動選擇', '選運動'):
        return 'menu_sport_events', None, 0, None

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
        '━━━━━━━━━━━━━━━\n'
        '🏆 新紀元\n'
        '體育即時分析平台\n'
        '━━━━━━━━━━━━━━━\n'
        '\n'
        '▸ 查看賽事\n'
        '  點擊「🏆 今日賽事」按鈕\n'
        '  或輸入「籃球」「棒球」「足球」...\n'
        '\n'
        '▸ 智能分析\n'
        '  在賽事列表中點擊比賽按鈕\n'
        '  或輸入「分析 隊名」\n'
        '\n'
        '▸ 查看不同日期\n'
        '  點擊「📅 明日賽事」按鈕\n'
        '  或輸入「明天 籃球」\n'
        '\n'
        '━━━━━━━━━━━━━━━\n'
        '📡 資料來源：playsport.cc'
    )


def find_game_by_keyword(games, keyword):
    """根據關鍵字找到匹配的比賽（優先完全匹配，再部分匹配）"""
    if not keyword:
        return []

    keyword = keyword.lower()
    exact = []
    partial = []
    for g in games:
        home = g.get('home', '').lower()
        away = g.get('away', '').lower()
        if keyword == home or keyword == away:
            exact.append(g)
        elif keyword in home or keyword in away:
            partial.append(g)

    return exact if exact else partial


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
        display_date = get_display_date(date_offset)
        if keyword:
            return (
                '❌ 查無賽事\n'
                '━━━━━━━━━━━━━━━\n'
                f'📅 {display_date}\n\n'
                f'找不到與「{keyword}」相關的賽事。\n\n'
                '▸ 請確認隊名是否正確\n'
                '▸ 或返回賽事列表重新選擇'
            )
        return (
            '❌ 查無賽事\n'
            '━━━━━━━━━━━━━━━\n'
            f'📅 {display_date}\n\n'
            '目前暫無賽事資料。'
        )

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
            f'📋 會員狀態{admin_tag}\n'
            f'━━━━━━━━━━━━━━━\n'
            f'{expiry}'
        )

    return (
        '📋 會員狀態\n'
        '━━━━━━━━━━━━━━━\n'
        '⚠️ 尚未開通會員資格\n\n'
        '▸ 請點擊「💰 儲值序號」按鈕\n'
        '  輸入序號來開通會員。'
    )


def handle_redeem(user_id, code):
    """儲值序號"""
    if not code:
        return (
            '💰 儲值序號\n'
            '━━━━━━━━━━━━━━━\n'
            '請直接貼上您的序號：\n\n'
            '▸ 格式：XXXX-XXXX-XXXX\n'
            '▸ 範例：AB12-CD34-EF56'
        )

    success, msg = redeem_code(user_id, code)
    icon = '✅' if success else '❌'
    return (
        f'{icon} 儲值結果\n'
        f'━━━━━━━━━━━━━━━\n'
        f'{msg}'
    )


def handle_query_uid(user_id):
    """查詢用戶 UID（隱藏指令）"""
    role = '👑 管理員' if is_admin(user_id) else '👤 一般用戶'
    member = get_member_expiry(user_id) or '⚠️ 未開通'
    return (
        f'🔑 用戶資訊\n'
        f'━━━━━━━━━━━━━━━\n'
        f'▸ 身份：{role}\n'
        f'▸ 會員：{member}\n\n'
        f'UID：\n{user_id}'
    )


def handle_set_admin(operator_uid, target_uid):
    """設為管理員（僅管理員可操作）"""
    if not is_admin(operator_uid):
        return (
            '⛔ 權限不足\n'
            '━━━━━━━━━━━━━━━\n'
            '僅管理員可執行此操作。'
        )
    if not target_uid:
        return (
            '👑 設為管理員\n'
            '━━━━━━━━━━━━━━━\n'
            '請提供目標用戶 UID\n\n'
            '▸ 格式：設為管理員 <UID>'
        )

    added = add_admin(target_uid)
    if added:
        return (
            '✅ 操作成功\n'
            '━━━━━━━━━━━━━━━\n'
            f'已將以下用戶設為管理員：\n'
            f'{target_uid[:20]}...'
        )
    return f'⚠️ {target_uid[:10]}... 已經是管理員。'


def handle_remove_admin(operator_uid, target_uid):
    """移除管理員"""
    if not is_admin(operator_uid):
        return (
            '⛔ 權限不足\n'
            '━━━━━━━━━━━━━━━\n'
            '僅管理員可執行此操作。'
        )
    if not target_uid:
        return '❌ 請提供目標用戶 UID。\n\n▸ 格式：移除管理員 <UID>'

    removed = remove_admin(target_uid)
    if removed:
        return (
            '✅ 操作成功\n'
            '━━━━━━━━━━━━━━━\n'
            f'已移除以下用戶的管理員資格：\n'
            f'{target_uid[:20]}...'
        )
    return f'⚠️ {target_uid[:10]}... 不是管理員。'


def handle_gen_code(operator_uid, params):
    """生成序號（僅管理員）
    params: '2D 1' 或 '7D 3' 等，格式為 <期限> <數量>
    """
    if not is_admin(operator_uid):
        return (
            '⛔ 權限不足\n'
            '━━━━━━━━━━━━━━━\n'
            '僅管理員可執行此操作。'
        )

    options_list = '\n'.join([f'  ▸ {k} = {v[1]}' for k, v in DURATION_OPTIONS.items()])
    usage_hint = f'可用期限：\n{options_list}\n\n範例：產生序號 7D 1'

    if not params:
        return (
            '🎫 產生序號\n'
            '━━━━━━━━━━━━━━━\n'
            f'{usage_hint}'
        )

    parts = params.strip().split()
    if len(parts) < 2:
        return (
            '⚠️ 格式錯誤\n'
            '━━━━━━━━━━━━━━━\n'
            '格式：產生序號 [期限] [數量]\n\n'
            f'{usage_hint}'
        )

    dur_key = parts[0].upper()
    if dur_key not in DURATION_OPTIONS:
        return (
            f'⚠️ 無效期限【{parts[0]}】\n'
            '━━━━━━━━━━━━━━━\n'
            f'{usage_hint}'
        )

    try:
        count = int(parts[1])
        if count < 1 or count > 20:
            return '⚠️ 數量請輸入 1~20'
    except ValueError:
        return '⚠️ 數量必須是數字'

    display_name = DURATION_OPTIONS[dur_key][1]
    codes = []
    for _ in range(count):
        code, _ = generate_code(operator_uid, dur_key)
        if code:
            codes.append(code)

    if not codes:
        return '❌ 序號生成失敗，請確認系統狀態。'

    code_list = '\n'.join(codes)
    return (
        f'✅ 已產生 {len(codes)} 組【{display_name}】序號\n'
        f'━━━━━━━━━━━━━━━\n'
        f'{code_list}\n\n'
        f'━━━━━━━━━━━━━━━\n'
        f'用戶輸入以下格式開通：\n'
        f'儲值序號 XXXX-XXXX-XXXX'
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
        QuickReplyItem(action=MessageAction(label='📋 昨日賽果', text='昨日賽果')),
        QuickReplyItem(action=MessageAction(label='� 今日賽果', text='今日賽果')),
        QuickReplyItem(action=MessageAction(label='� 儲值序號', text='儲值序號')),
    ]


def build_sport_qr(cmd_prefix):
    """第二層：選球類（通用）"""
    items = []
    for s in SPORT_OPTIONS:
        label = f'{s["emoji"]} {s["name"]}'
        items.append(QuickReplyItem(action=MessageAction(label=label, text=f'{cmd_prefix} {s["name"]}')))
    items.append(
        QuickReplyItem(action=MessageAction(label='↩ 返回主選單', text='返回主選單'))
    )
    return items


def build_league_qr(sport_key, cmd_prefix):
    """第三層：選聯賽"""
    leagues = PS_LEAGUES.get(sport_key, [])
    items = []
    for lg in leagues:
        label = lg['name']
        if len(label) > 12:
            label = label[:12]
        items.append(QuickReplyItem(action=MessageAction(
            label=label, text=f'{cmd_prefix} {lg["name"]}'
        )))
    items.append(
        QuickReplyItem(action=MessageAction(label='↩ 返回球類', text=cmd_prefix.rsplit(' ', 1)[0] if ' ' in cmd_prefix else cmd_prefix))
    )
    items.append(
        QuickReplyItem(action=MessageAction(label='🏠 主選單', text='返回主選單'))
    )
    return items


def build_game_qr(game_list, cmd_prefix=''):
    """第四層：每場比賽的分析按鈕"""
    game_buttons = []
    seen = set()
    for g in game_list:
        home = g.get('home', '')
        away = g.get('away', '')
        if home and home != '—' and home not in seen:
            vs_text = f'{away}v{home}' if away and away != '—' else home
            label = f'📊 {vs_text[:10]}' if len(vs_text) > 10 else f'📊 {vs_text}'
            game_buttons.append(
                QuickReplyItem(action=MessageAction(label=label, text=f'分析 {home}'))
            )
            seen.add(home)
        if len(game_buttons) >= 10:
            break
    # 返回聯賽選擇
    if cmd_prefix:
        back_cmd = ' '.join(cmd_prefix.split(' ')[:2])  # e.g. "今日賽事 籃球"
        game_buttons.append(
            QuickReplyItem(action=MessageAction(label='↩ 返回聯賽', text=back_cmd))
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

    # 如果用戶正在等待輸入序號，把整段訊息當作序號
    if uid in _user_waiting_redeem:
        _user_waiting_redeem.discard(uid)
        # 如果輸入的是其他指令，先檢查是否像序號格式
        if len(text) >= 6 and not text.startswith(('主選單', '選單', '返回', 'help', '幫助', '說明', '查詢', '儲值', '今日', '明日', '籃球', '棒球', '足球', '分析')):
            action, sport, date_offset, keyword = 'redeem', None, 0, text
        else:
            action, sport, date_offset, keyword = parse_user_message(text)
    else:
        action, sport, date_offset, keyword = parse_user_message(text)

    game_list = []
    qr_items = build_main_menu_qr()  # 預設回到第一層

    SPORT_DISPLAY = {'basketball': '籃球', 'baseball': '棒球', 'soccer': '足球',
                      'hockey': '冰球', 'tennis': '網球'}

    # 不需要會員的指令
    if action == 'main_menu':
        reply = (
            '━━━━━━━━━━━━━━━\n'
            '🏆 新紀元\n'
            '體育即時分析平台\n'
            '━━━━━━━━━━━━━━━\n'
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
        if not keyword:
            _user_waiting_redeem.add(uid)
        reply = handle_redeem(uid, keyword)

    # ===== 四層選單：第二層 選球類 =====
    elif action in ('menu_sport_events', 'menu_sport_results'):
        display_date = get_display_date(date_offset)
        cmd_map = {0: '今日賽事', 1: '明日賽事', -1: '昨日賽果'}
        if action == 'menu_sport_results' and date_offset == 0:
            cmd_label = '今日賽果'
        else:
            cmd_label = cmd_map.get(date_offset, '今日賽事')
        reply = (
            f'🏆 {cmd_label}\n'
            f'━━━━━━━━━━━━━━━\n'
            f'📅 {display_date}\n\n'
            f'👇 選擇要查看的球類'
        )
        qr_items = build_sport_qr(cmd_label)

    # ===== 四層選單：第三層 選聯賽 =====
    elif action in ('menu_league_events', 'menu_league_results'):
        if not is_member_active(uid):
            reply = (
                '🔒 權限不足\n'
                '━━━━━━━━━━━━━━━\n'
                '此功能需要會員資格\n\n'
                '▸ 請先儲值序號來開通會員\n'
                '  格式：儲值序號 XXXX-XXXX-XXXX\n\n'
                '▸ 輸入「查詢到期」可查看會員狀態'
            )
        else:
            display_date = get_display_date(date_offset)
            sport_name = SPORT_DISPLAY.get(sport, '')
            cmd_prefix = keyword  # e.g. "今日賽事"
            leagues = PS_LEAGUES.get(sport, [])
            league_names = ', '.join(lg['name'] for lg in leagues)
            reply = (
                f'🏆 {cmd_prefix} — {sport_name}\n'
                f'━━━━━━━━━━━━━━━\n'
                f'📅 {display_date}\n\n'
                f'📋 共 {len(leagues)} 個聯賽\n'
                f'{league_names}\n\n'
                f'👇 選擇要查看的聯賽'
            )
            qr_items = build_league_qr(sport, f'{cmd_prefix} {sport_name}')

    # ===== 四層選單：第四層 顯示聯賽賽事/賽果 =====
    elif action in ('menu_games_events', 'menu_games_results'):
        if not is_member_active(uid):
            reply = (
                '🔒 權限不足\n'
                '━━━━━━━━━━━━━━━\n'
                '此功能需要會員資格\n\n'
                '▸ 請先儲值序號來開通會員\n'
                '  格式：儲值序號 XXXX-XXXX-XXXX\n\n'
                '▸ 輸入「查詢到期」可查看會員狀態'
            )
        else:
            league_name = keyword  # e.g. "NBA"
            gamedate = get_date_str(date_offset)
            display_date = get_display_date(date_offset)
            all_games = get_games_cached(sport, gamedate)
            # 篩選該聯賽的比賽
            league_games = [g for g in all_games if g.get('league') == league_name]
            _user_session[uid] = {'date_offset': date_offset, 'sport': sport}

            sport_name = SPORT_DISPLAY.get(sport, '')
            cmd_map = {0: '今日賽事', 1: '明日賽事', -1: '昨日賽果'}
            if action == 'menu_games_results' and date_offset == 0:
                cmd_label = '今日賽果'
            else:
                cmd_label = cmd_map.get(date_offset, '今日賽事')
            cmd_prefix = f'{cmd_label} {sport_name} {league_name}'

            if action == 'menu_games_results':
                # 賽果模式：用 format_yesterday_results 顯示結算
                reply = format_yesterday_results(league_games, sport, display_date)
                qr_items = build_league_qr(sport, f'{cmd_label} {sport_name}')
            else:
                # 賽事模式：用 format_league_games_text 顯示賽事
                reply = format_league_games_text(league_games, league_name, sport, display_date)
                qr_items = build_game_qr(league_games, cmd_prefix)

    # ===== 舊的直接輸入運動名稱（相容）=====
    elif action in ('list', 'analysis'):
        if not is_member_active(uid):
            reply = (
                '🔒 權限不足\n'
                '━━━━━━━━━━━━━━━\n'
                '此功能需要會員資格\n\n'
                '▸ 請先儲值序號來開通會員\n'
                '  格式：儲值序號 XXXX-XXXX-XXXX\n\n'
                '▸ 輸入「查詢到期」可查看會員狀態'
            )
        elif action == 'list':
            # 直接輸入球類名稱 → 跳到第三層選聯賽
            sport_name = SPORT_DISPLAY.get(sport or '', '')
            cmd_prefix = f'今日賽事 {sport_name}'
            if date_offset == 1:
                cmd_prefix = f'明日賽事 {sport_name}'
            elif date_offset == -1:
                cmd_prefix = f'昨日賽果 {sport_name}'
            display_date = get_display_date(date_offset)
            leagues = PS_LEAGUES.get(sport, [])
            league_names = ', '.join(lg['name'] for lg in leagues)
            reply = (
                f'🏆 {cmd_prefix}\n'
                f'━━━━━━━━━━━━━━━\n'
                f'📅 {display_date}\n\n'
                f'📋 共 {len(leagues)} 個聯賽\n'
                f'{league_names}\n\n'
                f'👇 選擇要查看的聯賽'
            )
            qr_items = build_league_qr(sport, cmd_prefix)
        elif action == 'analysis':
            session = _user_session.get(uid, {})
            if date_offset == 0 and session.get('date_offset'):
                date_offset = session['date_offset']
            if not sport and session.get('sport'):
                sport = session['sport']
            reply = handle_analysis(sport, date_offset, keyword)
    else:
        reply = build_help_message()

    # LINE 每條訊息限制 5000 字，reply 最多 5 條
    quick_reply = QuickReply(items=qr_items[:13])

    if len(reply) <= 4500:
        messages = [TextMessage(text=reply, quick_reply=quick_reply)]
    else:
        # 按聯賽區塊分割（雙換行）
        chunks = []
        current = ''
        for block in reply.split('\n\n'):
            if current and len(current) + len(block) + 2 > 4500:
                chunks.append(current.strip())
                current = block
            else:
                current = current + '\n\n' + block if current else block
        if current.strip():
            chunks.append(current.strip())

        # LINE 最多 5 條訊息
        chunks = chunks[:5]
        messages = []
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                messages.append(TextMessage(text=chunk, quick_reply=quick_reply))
            else:
                messages.append(TextMessage(text=chunk))

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages
            )
        )


# ===== Keep-Alive 防止休眠 =====
def keep_alive():
    """每 10 分鐘 ping 自己的 /health，防止 Render 免費方案休眠"""
    url = os.environ.get('RENDER_EXTERNAL_URL')
    if url:
        try:
            urllib.request.urlopen(f'{url}/health', timeout=10)
            print(f'[KeepAlive] Pinged {url}/health')
        except Exception as e:
            print(f'[KeepAlive] Ping failed: {e}')
    timer = threading.Timer(600, keep_alive)  # 600秒 = 10分鐘
    timer.daemon = True
    timer.start()

# 啟動 keep-alive
keep_alive()


# ===== 啟動 =====
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'========================================')
    print(f'  新紀元 LINE Bot')
    print(f'  http://localhost:{port}')
    print(f'========================================')
    print(f'  Webhook URL: https://你的網域/callback')
    print(f'  Health:      http://localhost:{port}/health')
    print(f'========================================')
    app.run(host='0.0.0.0', port=port, debug=False)
