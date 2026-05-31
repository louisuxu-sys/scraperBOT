"""
球員狀態資料模組
從 ESPN API 爬取 NBA 傷兵報告與 MLB 先發投手資訊
"""
import requests
import threading
from datetime import datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8',
}

# ─── 隊名對照（中文 → ESPN 英文關鍵字）───────────────────────────────
NBA_TEAM_ZH_TO_EN = {
    '塞爾提克': 'Celtics', '籃網': 'Nets', '尼克': 'Knicks',
    '76人': '76ers', '暴龍': 'Raptors', '公牛': 'Bulls',
    '騎士': 'Cavaliers', '活塞': 'Pistons', '溜馬': 'Pacers',
    '公鹿': 'Bucks', '老鷹': 'Hawks', '黃蜂': 'Hornets',
    '熱火': 'Heat', '魔術': 'Magic', '巫師': 'Wizards',
    '金塊': 'Nuggets', '灰狼': 'Timberwolves', '雷霆': 'Thunder',
    '拓荒者': 'Trail Blazers', '爵士': 'Jazz', '勇士': 'Warriors',
    '快艇': 'Clippers', '湖人': 'Lakers', '太陽': 'Suns',
    '國王': 'Kings', '獨行俠': 'Mavericks', '小牛': 'Mavericks',
    '火箭': 'Rockets', '灰熊': 'Grizzlies', '鵜鶘': 'Pelicans',
    '馬刺': 'Spurs',
}

MLB_TEAM_ZH_TO_EN = {
    '金鶯': 'Orioles', '紅襪': 'Red Sox', '洋基': 'Yankees',
    '光芒': 'Rays', '藍鳥': 'Blue Jays', '白襪': 'White Sox',
    '守護者': 'Guardians', '老虎': 'Tigers', '皇家': 'Royals',
    '雙城': 'Twins', '太空人': 'Astros', '天使': 'Angels',
    '運動家': 'Athletics', '水手': 'Mariners', '遊騎兵': 'Rangers',
    '勇士': 'Braves', '馬林魚': 'Marlins', '大都會': 'Mets',
    '費城人': 'Phillies', '國民': 'Nationals', '小熊': 'Cubs',
    '紅人': 'Reds', '釀酒人': 'Brewers', '海盜': 'Pirates',
    '紅雀': 'Cardinals', '響尾蛇': 'Diamondbacks', '落磯': 'Rockies',
    '道奇': 'Dodgers', '教士': 'Padres', '巨人': 'Giants',
}

# ─── 快取 ──────────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()
INJURY_TTL = 1800   # 傷兵報告 30 分鐘
PITCHER_TTL = 3600  # 先發投手 60 分鐘


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        data, ts, ttl = entry
        if (datetime.now() - ts).total_seconds() < ttl:
            return data
        return None


def _cache_set(key, data, ttl):
    with _cache_lock:
        _cache[key] = (data, datetime.now(), ttl)


# ─── ESPN API 爬取 ─────────────────────────────────────────────────

def fetch_nba_injuries():
    """
    從 ESPN API 取得 NBA 傷兵報告
    回傳：{隊名（英文 displayName）: [{'player', 'status', 'injury_type'}]}
    API 結構：injuries[].displayName（隊名），injuries[].injuries[]（傷兵清單）
    """
    cached = _cache_get('nba_injuries')
    if cached is not None:
        return cached

    result = {}
    try:
        url = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries'
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        for team_entry in data.get('injuries', []):
            # 隊名直接在頂層 displayName（非 team 子物件）
            team_name = team_entry.get('displayName', '')
            injuries = []
            for inj in team_entry.get('injuries', []):
                athlete = inj.get('athlete', {})
                status = inj.get('status', '')
                details = inj.get('details', {})
                player_name = athlete.get('displayName', '')
                injury_type = details.get('type', '')

                if player_name and status in ('Out', 'Doubtful', 'Questionable', 'Day-To-Day'):
                    injuries.append({
                        'player': player_name,
                        'status': status,
                        'injury_type': injury_type,
                    })

            if team_name and injuries:
                result[team_name] = injuries

    except Exception as e:
        print(f'[PlayerData] NBA injuries error: {e}')

    _cache_set('nba_injuries', result, INJURY_TTL)
    return result


def fetch_mlb_probable_pitchers(gamedate=None):
    """
    從 ESPN API 取得 MLB 先發投手
    回傳：{(away_en_name, home_en_name): {'away': pitcher_dict, 'home': pitcher_dict}}
    pitcher_dict: {'name': str, 'era': str, 'wins': str, 'losses': str}
    """
    if not gamedate:
        now = datetime.now(TW_TZ)
        gamedate = now.strftime('%Y%m%d')

    cache_key = f'mlb_pitchers_{gamedate}'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result = {}
    try:
        url = f'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={gamedate}'
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        for event in data.get('events', []):
            comps = event.get('competitions', [])
            if not comps:
                continue
            comp = comps[0]
            teams = {}
            pitchers = {}

            for competitor in comp.get('competitors', []):
                side = competitor.get('homeAway', '')  # 'home' or 'away'
                team = competitor.get('team', {})
                teams[side] = team.get('displayName', '')

                probables = competitor.get('probables', [])
                if probables:
                    p = probables[0]
                    athlete = p.get('athlete', {})
                    stats_raw = p.get('statistics', [])
                    stats = {s['name']: s.get('displayValue', '') for s in stats_raw if 'name' in s}
                    pitchers[side] = {
                        'name': athlete.get('fullName', athlete.get('displayName', '')),
                        'era': stats.get('ERA', ''),
                        'wins': stats.get('wins', ''),
                        'losses': stats.get('losses', ''),
                    }

            away = teams.get('away', '')
            home = teams.get('home', '')
            if away and home:
                result[(away, home)] = {
                    'away': pitchers.get('away'),
                    'home': pitchers.get('home'),
                }

    except Exception as e:
        print(f'[PlayerData] MLB pitchers error: {e}')

    _cache_set(cache_key, result, PITCHER_TTL)
    return result


# ─── 查詢介面 ──────────────────────────────────────────────────────

def get_nba_team_injuries(team_zh):
    """
    用中文隊名查詢 NBA 傷兵清單
    回傳：[{'player', 'status', 'injury_type'}] 或 []
    """
    try:
        data = fetch_nba_injuries()
        en_kw = NBA_TEAM_ZH_TO_EN.get(team_zh, '').lower()
        if not en_kw:
            return []
        for team_name, injuries in data.items():
            if en_kw in team_name.lower():
                return injuries
        return []
    except Exception:
        return []


def get_mlb_game_pitchers(away_zh, home_zh, gamedate=None):
    """
    用中文隊名查詢 MLB 先發投手
    回傳：{'away': pitcher_dict, 'home': pitcher_dict} 或 None
    """
    try:
        data = fetch_mlb_probable_pitchers(gamedate)
        away_en = MLB_TEAM_ZH_TO_EN.get(away_zh, away_zh).lower()
        home_en = MLB_TEAM_ZH_TO_EN.get(home_zh, home_zh).lower()
        for (a, h), info in data.items():
            if away_en in a.lower() and home_en in h.lower():
                return info
        return None
    except Exception:
        return None


# ─── 評估函式 ──────────────────────────────────────────────────────

def calc_injury_impact(injuries):
    """
    計算傷兵影響分（回傳負數，越低代表該隊傷情越嚴重）
    Out: -5, Doubtful: -3, Questionable/Day-To-Day: -1
    上限 -15（避免過度懲罰）
    """
    impact = 0
    for inj in injuries:
        s = inj.get('status', '')
        if s == 'Out':
            impact -= 5
        elif s == 'Doubtful':
            impact -= 3
        elif s in ('Questionable', 'Day-To-Day'):
            impact -= 1
    return max(impact, -15)


def calc_pitcher_quality(pitcher_info):
    """
    依 ERA 評估先發投手品質，回傳調整分（正數有利該隊）
    ERA ≤2.50: +8, ≤3.25: +5, ≤4.00: +2, ≤5.00: -2, >5.00: -5
    無 ERA 但有投手名字：+1（至少有已知先發的微加分）
    """
    if not pitcher_info or not pitcher_info.get('name'):
        return 0
    era_str = pitcher_info.get('era', '')
    try:
        era = float(era_str)
        if era <= 2.50:
            return 8
        elif era <= 3.25:
            return 5
        elif era <= 4.00:
            return 2
        elif era <= 5.00:
            return -2
        else:
            return -5
    except (ValueError, TypeError):
        return 1
