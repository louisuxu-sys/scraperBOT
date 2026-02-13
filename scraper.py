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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}


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

    # 合併
    for game in games:
        gid = game.get('gameId')
        if gid and gid in score_data:
            sd = score_data[gid]
            if sd.get('awayScore') is not None:
                game['awayScore'] = sd['awayScore']
            if sd.get('homeScore') is not None:
                game['homeScore'] = sd['homeScore']
            if sd.get('status'):
                game['status'] = sd['status']
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

            left_m = re.search(r'team_left[^>]*>[\s\S]*?<a[^>]*>\s*([\s\S]*?)\s*</a>', ph)
            right_m = re.search(r'team_right[^>]*>[\s\S]*?<a[^>]*>\s*([\s\S]*?)\s*</a>', ph)
            center_m = re.search(r'team_cinter[^>]*>\s*([^<]+)', ph)
            if left_m:
                away = left_m.group(1).strip()
            if right_m:
                home = right_m.group(1).strip()
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
        if box_start > -1 and box_end > -1:
            box_html = html[box_start:box_end]
            sp = re.search(r'data-aheadprice="([^"]*)"', box_html)
            so = re.search(r'data-aheadodds="([^"]*)"', box_html)
            if sp:
                odds['spread'] = sp.group(1)
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

        if away or home:
            date_str = f'{gamedate[:4]}-{gamedate[4:6]}-{gamedate[6:8]}'
            games.append({
                'id': f'ps_{ps_id}_{gamedate}_{box["id"]}',
                'gameId': box['id'],
                'oid': box['oid'],
                'league': league_name,
                'leagueId': ps_id,
                'away': away or '—',
                'home': home or '—',
                'awayScore': None,
                'homeScore': None,
                'date': date_str,
                'time': time_str,
                'status': 'upcoming',
                'record': record,
                'odds': odds,
                'teamCodes': team_codes,
                'quarterScores': None,
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
                'away': sg['away'],
                'home': sg['home'],
                'awayScore': None,
                'homeScore': None,
                'date': date_str,
                'time': '',
                'status': 'upcoming',
                'record': {},
                'odds': {},
                'teamCodes': '',
                'quarterScores': None,
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

        if away_score is not None and home_score is not None:
            if 'gamebox-notend' in gb_html:
                status = 'live'
            else:
                status = 'finished'

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

    # 排序：live > upcoming > postponed > finished
    status_order = {'live': 0, 'upcoming': 1, 'postponed': 2, 'finished': 3}
    all_games.sort(key=lambda g: (status_order.get(g['status'], 9), g.get('time', '')))

    return all_games


if __name__ == '__main__':
    # 測試用
    games = fetch_all_games('basketball')
    for g in games:
        score = f"{g['homeScore']}:{g['awayScore']}" if g['homeScore'] is not None else 'VS'
        print(f"[{g['status']}] {g['away']} vs {g['home']} | {score} | {g['league']}")
