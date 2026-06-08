"""
playsport.cc 賽事資料爬取模組
從 playsport.cc 爬取即時比分、戰績、盤口等資料
"""
import re
import requests
from datetime import datetime, timedelta, timezone

# 台灣時區 UTC+8
TW_TZ = timezone(timedelta(hours=8))

# playsport.cc 聯賽 ID 對照
PS_LEAGUES = {
    'basketball': [
        {'psId': '3', 'name': 'NBA'},
        {'psId': '8', 'name': '歐洲職籃'},
        {'psId': '89', 'name': 'SBL'},
        {'psId': '92', 'name': '韓國職籃'},
        {'psId': '97', 'name': '日本職籃'},
    ],
    'baseball': [
        {'psId': '1', 'name': 'MLB'},
        {'psId': '2', 'name': '日本職棒'},
        {'psId': '6', 'name': '中華職棒'},
        {'psId': '9', 'name': '韓國職棒'},
    ],
    'soccer': [
        {'psId': '4', 'name': '足球'},
    ],
    'hockey': [
        {'psId': '91', 'name': 'NHL冰球'},
    ],
    'tennis': [
        {'psId': '21', 'name': '網球'},
    ],
}

# 中立場地聯賽 ID（無主客場之分）
NEUTRAL_LEAGUE_IDS = set()  # 目前無中立場地聯賽

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}

# playsport.cc 隊名修正對照表（網站會截斷隊名 + MLB英文隊名轉中文）
TEAM_NAME_FIX = {
    # NBA
    '塞爾提': '塞爾提克',
    # WBC 國家名截斷修正
    '哥倫比': '哥倫比亞', '尼加拉': '尼加拉瓜', '波多黎': '波多黎各',
    '委內瑞': '委內瑞拉', '多明尼': '多明尼加', '澳大利': '澳大利亞',
    '紐西蘭': '紐西蘭', '多明尼加共和': '多明尼加',
    # MLB 英文→中文
    '費城人': '費城人', '勇士': '勇士', '光芒': '光芒', '紅雀': '紅雀',
    '老虎': '老虎', '雙城': '雙城', '大都會': '大都會', '遊騎兵': '遊騎兵',
    '道奇': '道奇', '紅人': '紅人', '教士': '教士', '天使': '天使',
    '落磯': '落磯', '國民': '國民',
    'Phillies': '費城人', 'Braves': '勇士', 'Rays': '光芒',
    'Cardinals': '紅雀', 'Tigers': '老虎', 'Twins': '雙城',
    'Mets': '大都會', 'Rangers': '遊騎兵', 'Dodgers': '道奇',
    'Reds': '紅人', 'Padres': '教士', 'Angels': '天使',
    'Rockies': '落磯', 'Nationals': '國民',
    'Yankees': '洋基', 'Red Sox': '紅襪', 'Blue Jays': '藍鳥',
    'Orioles': '金鶯', 'Astros': '太空人', 'Mariners': '水手',
    'Athletics': '運動家', 'White Sox': '白襪', 'Guardians': '守護者',
    'Royals': '皇家', 'Cubs': '小熊', 'Brewers': '釀酒人',
    'Pirates': '海盜', 'Marlins': '馬林魚', 'Diamondbacks': '響尾蛇',
    'Giants': '巨人',
    # 可依需要繼續擴充
}


def fix_team_name(name):
    """修正 playsport.cc 截斷的隊名"""
    return TEAM_NAME_FIX.get(name, name)


def get_league_name(ps_id):
    """根據聯賽 ID 取得名稱"""
    for sport_leagues in PS_LEAGUES.values():
        for league in sport_leagues:
            if league['psId'] == ps_id:
                return league['name']
    return '未知聯賽'


def fetch_playsport(ps_id, gamedate=None):
    """
    從 playsport.cc 爬取賽事資料
    ps_id: 聯賽 ID (e.g. '3' for NBA)
    gamedate: 日期字串 'YYYYMMDD'，預設今天
    回傳: list of game dicts
    """
    if not gamedate:
        now = datetime.now(TW_TZ)
        gamedate = now.strftime('%Y%m%d')

    league_name = get_league_name(ps_id)
    live_url = f'https://www.playsport.cc/livescore/{ps_id}?gamedate={gamedate}'
    pre_url = f'https://www.playsport.cc/livescore/{ps_id}?gamedate={gamedate}&mode=2'

    try:
        live_resp = requests.get(live_url, headers=HEADERS, timeout=15)
        live_resp.encoding = 'utf-8'
        live_html = live_resp.text

        pre_resp = requests.get(pre_url, headers=HEADERS, timeout=15)
        pre_resp.encoding = 'utf-8'
        pre_html = pre_resp.text
    except Exception as e:
        print(f'[Scraper] fetch error: {e}')
        return []

    # 從 mode=2 取得賽前資料
    games = parse_pre_html(pre_html, ps_id, gamedate, league_name)

    # 從 live 模式取得比分和狀態
    score_data = parse_live_scores(live_html)

    # 合併：只有當 status 有值（live/finished）時才更新比分
    for game in games:
        gid = game.get('gameId')
        if gid and gid in score_data:
            sd = score_data[gid]
            if sd.get('status'):
                # 有明確狀態（live/finished）才更新比分和狀態
                game['status'] = sd['status']
                if sd.get('awayScore') is not None:
                    game['awayScore'] = sd['awayScore']
                if sd.get('homeScore') is not None:
                    game['homeScore'] = sd['homeScore']
                if sd.get('quarterScores'):
                    game['quarterScores'] = sd['quarterScores']

    return games


def parse_pre_html(html, ps_id, gamedate, league_name):
    """解析 mode=2 頁面（賽前資料：隊名、戰績、盤口）"""
    games = []

    # 從 select#gamebattle 取得賽事清單（備援用）
    select_games = []
    for m in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', html):
        val, text = m.group(1), m.group(2).strip()
        if not val or val == '0' or 'vs' not in text:
            continue
        parts = re.split(r'\s*vs\s*', text)
        if len(parts) == 2:
            select_games.append({'value': val, 'away': parts[0].strip(), 'home': parts[1].strip()})

    # 從 outer-gamebox 取得詳細資料
    boxes = []
    for m in re.finditer(r'id="outer-gamebox-(\d+)"[^>]*data-oid="([^"]*)"', html):
        boxes.append({'id': m.group(1), 'oid': m.group(2)})

    for box in boxes:
        away, home, time_str = '', '', ''
        record = {}
        odds = {}
        team_codes = (box['oid'].split('_')[2]) if len(box['oid'].split('_')) > 2 else ''

        # 從 previewBox 取得隊名和時間
        preview_start = html.find(f'id="gamebox-preview-{box["id"]}"')
        if preview_start > -1:
            preview_end = html.find('開打前的gamebox END', preview_start)
            ph = html[preview_start:preview_end] if preview_end > -1 else html[preview_start:preview_start + 15000]

            # 隊名解析：取 <td> 全部內容後去 HTML 標籤（相容一般聯賽與 WBC 國際賽）
            left_m = re.search(r'team_left[^>]*>([\s\S]*?)</td>', ph)
            right_m = re.search(r'team_right[^>]*>([\s\S]*?)</td>', ph)
            center_m = re.search(r'team_cinter[^>]*>\s*([^<]+)', ph)
            if left_m:
                away = re.sub(r'<[^>]+>', '', left_m.group(1)).strip()
            if right_m:
                home = re.sub(r'<[^>]+>', '', right_m.group(1)).strip()
            if center_m:
                time_str = center_m.group(1).strip()

            # 戰績
            def extract_stat(label):
                pattern = rf'datd_c[^>]*>\s*{label}[\s\S]*?datd_l[^>]*>([\s\S]*?)</td>[\s\S]*?datd_r[^>]*>([\s\S]*?)</td>'
                sm = re.search(pattern, ph)
                if sm:
                    left = re.sub(r'<[^>]+>', '', sm.group(1)).replace('詳細比分', '').strip()
                    right = re.sub(r'<[^>]+>', '', sm.group(2)).replace('詳細比分', '').strip()
                    return {'left': left, 'right': right}
                return None

            rec = extract_stat('戰績')
            if rec:
                record['awayRecord'] = rec['left']
                record['homeRecord'] = rec['right']
            recent = extract_stat('近十場')
            if recent:
                record['awayRecent'] = recent['left']
                record['homeRecent'] = recent['right']
            h2h = extract_stat('對戰紀錄')
            if h2h:
                record['awayH2H'] = h2h['left']
                record['homeH2H'] = h2h['right']
            avg = extract_stat(r'平均得 \/ 失分')
            if avg:
                record['awayAvg'] = avg['left']
                record['homeAvg'] = avg['right']
            ha = extract_stat(r'主 \/ 客戰績')
            if ha:
                record['awayHomeAway'] = ha['left']
                record['homeHomeAway'] = ha['right']

        # 盤口
        box_start = html.find(f'id="outer-gamebox-{box["id"]}"')
        box_end = html.find('</div><!--outer-gamebox-->', box_start)
        # 部分聯賽（如足球）沒有 <!--outer-gamebox--> 結束標記，改用下一個 outer-gamebox 作邊界
        if box_start > -1 and box_end == -1:
            next_box_start = html.find('id="outer-gamebox-', box_start + 20)
            box_end = next_box_start if next_box_start > box_start else box_start + 8000
        if box_start > -1 and box_end > -1:
            box_html = html[box_start:box_end]
            sp = re.search(r'data-aheadprice="([^"]*)"', box_html)
            so = re.search(r'data-aheadodds="([^"]*)"', box_html)
            if sp:
                odds['spread'] = sp.group(1)
                odds['spread_unsigned'] = True  # livescore 盤口無正負號
            if so:
                odds['spreadOdds'] = so.group(1)

            # 隊名備援
            if not away or not home:
                nh = re.search(r'data-nameh="([^"]*)"', box_html)
                na = re.search(r'data-namea="([^"]*)"', box_html)
                if not home and nh:
                    home = nh.group(1)
                if not away and na:
                    away = na.group(1)

            # 時間備援
            if not time_str:
                tm = re.search(r'比賽時間[\s\S]*?(\d{1,2}:\d{2})', box_html)
                if tm:
                    time_str = tm.group(1)

        # select 備援
        if not away or not home:
            for sg in select_games:
                if sg['value'] == box['oid']:
                    away = away or sg['away']
                    home = home or sg['home']
                    break

        # 直接偵測比賽狀態（不依賴 parse_live_scores 合併）
        game_status = 'upcoming'
        away_score = None
        home_score = None
        if box_start > -1:
            box_html_full = html[box_start:box_end] if box_end > box_start else html[box_start:box_start + 8000]
            # 檢查 onbox div 是否可見（開打後顯示）
            onbox_tag = re.search(
                rf'<div[^>]*id="gamebox-{box["id"]}"(?!-)[^>]*>',
                box_html_full,
            )
            if onbox_tag:
                tag_str = onbox_tag.group(0)
                style_m = re.search(r'style="([^"]*)"', tag_str)
                class_m = re.search(r'class="([^"]*)"', tag_str)
                onbox_visible = style_m and 'block' in style_m.group(1)
                onbox_notend = class_m and 'gamebox-notend' in class_m.group(1)
                if onbox_visible:
                    game_status = 'live' if onbox_notend else 'finished'
                    # 嘗試從 HTML 取得比分（先找 _big 版，再找一般版）
                    asr = (re.search(rf'id="{box["id"]}_asr_big"[^>]*>(\d+)<', box_html_full) or
                           re.search(rf'id="{box["id"]}_asr"[^>]*>(\d+)<', box_html_full))
                    hsr = (re.search(rf'id="{box["id"]}_hsr_big"[^>]*>(\d+)<', box_html_full) or
                           re.search(rf'id="{box["id"]}_hsr"[^>]*>(\d+)<', box_html_full))
                    if asr:
                        away_score = int(asr.group(1))
                    if hsr:
                        home_score = int(hsr.group(1))

        if away or home:
            date_str = f'{gamedate[:4]}-{gamedate[4:6]}-{gamedate[6:8]}'
            games.append({
                'id': f'ps_{ps_id}_{gamedate}_{box["id"]}',
                'gameId': box['id'],
                'oid': box['oid'],
                'league': league_name,
                'leagueId': ps_id,
                'away': fix_team_name(away) if away else '—',
                'home': fix_team_name(home) if home else '—',
                'awayScore': away_score,
                'homeScore': home_score,
                'date': date_str,
                'time': time_str,
                'status': game_status,
                'record': record,
                'odds': odds,
                'teamCodes': team_codes,
                'quarterScores': None,
                'neutral': ps_id in NEUTRAL_LEAGUE_IDS,
            })

    # 如果 gamebox 解析失敗，用 select 備援
    if not games:
        date_str = f'{gamedate[:4]}-{gamedate[4:6]}-{gamedate[6:8]}'
        for idx, sg in enumerate(select_games):
            games.append({
                'id': f'ps_{ps_id}_{gamedate}_sel_{idx}',
                'gameId': f'sel_{idx}',
                'oid': sg['value'],
                'league': league_name,
                'leagueId': ps_id,
                'away': fix_team_name(sg['away']),
                'home': fix_team_name(sg['home']),
                'awayScore': None,
                'homeScore': None,
                'date': date_str,
                'time': '',
                'status': 'upcoming',
                'record': {},
                'odds': {},
                'teamCodes': '',
                'quarterScores': None,
                'neutral': ps_id in NEUTRAL_LEAGUE_IDS,
            })

    return games


def parse_live_scores(html):
    """從預設模式 HTML 解析比分和狀態"""
    score_data = {}

    # 收集所有 gameId
    game_ids = re.findall(r'id="outer-gamebox-(\d+)"', html)

    for game_id in game_ids:
        away_score = None
        home_score = None
        status = None
        quarter_scores = {'away': [], 'home': []}

        # 總分（大比分）
        asr_big = re.search(rf'id="{game_id}_asr_big"[^>]*>(\d+)<', html)
        hsr_big = re.search(rf'id="{game_id}_hsr_big"[^>]*>(\d+)<', html)
        if asr_big:
            away_score = int(asr_big.group(1))
        if hsr_big:
            home_score = int(hsr_big.group(1))

        # 備援
        if away_score is None:
            asr = re.search(rf'id="{game_id}_asr"[^>]*>(\d+)<', html)
            if asr:
                away_score = int(asr.group(1))
        if home_score is None:
            hsr = re.search(rf'id="{game_id}_hsr"[^>]*>(\d+)<', html)
            if hsr:
                home_score = int(hsr.group(1))

        # 節比分（_as1/_hs1 或 _a1/_h1）
        for q in range(1, 9):
            aq = re.search(rf'id="{game_id}_as{q}"[^>]*>(\d+)<', html) or \
                 re.search(rf'id="{game_id}_a{q}"[^>]*>(\d+)<', html)
            hq = re.search(rf'id="{game_id}_hs{q}"[^>]*>(\d+)<', html) or \
                 re.search(rf'id="{game_id}_h{q}"[^>]*>(\d+)<', html)
            if aq:
                quarter_scores['away'].append(int(aq.group(1)))
            if hq:
                quarter_scores['home'].append(int(hq.group(1)))

        # 判斷狀態
        gb_start = html.find(f'id="outer-gamebox-{game_id}"')
        gb_end = html.find('<!--outer-gamebox-->', gb_start)
        gb_html = html[gb_start:gb_end] if gb_start > -1 and gb_end > -1 else ''

        # 狀態判斷：以實際顯示的區塊為準（preview / onbox）
        # playsport HTML 結構（class 在 id 前面）：
        #   preview: <div class="...gamebox-notend" id="gamebox-preview-XXXX" style="display:block;">
        #   onbox:   <div class="...gamebox-notend" id="gamebox-XXXX" ...     style="display:none;">
        # 注意：兩個 div 都可能帶 gamebox-notend class
        status = None
        if gb_html:
            # 提取 preview 整個 <div> 開頭標籤
            preview_tag = re.search(
                rf'<div[^>]*id="gamebox-preview-{game_id}"[^>]*>',
                gb_html,
            )
            preview_visible = False
            if preview_tag:
                tag_str = preview_tag.group(0)
                style_m = re.search(r'style="([^"]*)"', tag_str)
                if style_m:
                    preview_visible = 'block' in style_m.group(1)

            # 提取 onbox 整個 <div> 開頭標籤（用 (?!preview) 排除 preview）
            onbox_tag = re.search(
                rf'<div[^>]*id="gamebox-{game_id}"(?!-)[^>]*>',
                gb_html,
            )
            onbox_visible = False
            onbox_notend = False
            if onbox_tag:
                tag_str = onbox_tag.group(0)
                style_m = re.search(r'style="([^"]*)"', tag_str)
                if style_m:
                    onbox_visible = 'block' in style_m.group(1)
                class_m = re.search(r'class="([^"]*)"', tag_str)
                if class_m:
                    onbox_notend = 'gamebox-notend' in class_m.group(1)

            if onbox_visible:
                # 開打後的區塊正在顯示
                status = 'live' if onbox_notend else 'finished'
            elif preview_visible:
                # 賽前區塊正在顯示 → 未開始
                status = None  # 保持 upcoming
            else:
                # 都沒匹配到（備援：有真實比分就判 live）
                if away_score is not None and home_score is not None and \
                   (away_score > 0 or home_score > 0):
                    status = 'live'

        has_qs = len(quarter_scores['away']) > 0 or len(quarter_scores['home']) > 0
        score_data[game_id] = {
            'awayScore': away_score,
            'homeScore': home_score,
            'status': status,
            'quarterScores': quarter_scores if has_qs else None,
        }

    return score_data


def fetch_all_games(sport='basketball', gamedate=None):
    """
    爬取某個運動的所有聯賽賽事
    sport: 'basketball', 'baseball', 'soccer', 'hockey', 'tennis'
    gamedate: 'YYYYMMDD' 格式，預設今天
    """
    if not gamedate:
        now = datetime.now(TW_TZ)
        gamedate = now.strftime('%Y%m%d')

    leagues = PS_LEAGUES.get(sport, [])
    all_games = []
    for league in leagues:
        try:
            games = fetch_playsport(league['psId'], gamedate)
            all_games.extend(games)
            print(f'[Scraper] {league["name"]}: {len(games)} 場')
        except Exception as e:
            print(f'[Scraper] {league["name"]} error: {e}')

    # 去重：確保同一場比賽不重複出現
    seen_ids = set()
    unique_games = []
    for game in all_games:
        # 優先使用 gameId 作為唯一識別
        game_id = game.get('gameId', '')
        
        # 如果沒有 gameId，用隊名+時間+日期作為備用
        if not game_id:
            key = f"{game['away']}_{game['home']}_{game['time']}_{game['date']}"
            game_id = key
        
        if game_id not in seen_ids:
            seen_ids.add(game_id)
            unique_games.append(game)
    
    # 排序：live > upcoming > postponed > finished
    status_order = {'live': 0, 'upcoming': 1, 'postponed': 2, 'finished': 3}
    unique_games.sort(key=lambda g: (status_order.get(g['status'], 9), g.get('time', '')))

    return unique_games


if __name__ == '__main__':
    # 測試用
    games = fetch_all_games('basketball')
    for g in games:
        score = f"{g['homeScore']}:{g['awayScore']}" if g['homeScore'] is not None else 'VS'
        print(f"[{g['status']}] {g['away']} vs {g['home']} | {score} | {g['league']}")
