"""
Yahoo 台灣體育補充資料模組
爬取 MLB 球場天氣、勝敗投手，及 CPBL 賽事資訊作為分析參考
"""
import requests
import re
import threading
from datetime import datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8',
    'Referer': 'https://tw.sports.yahoo.com/',
}

SPORT_URLS = {
    'mlb':  'https://tw.sports.yahoo.com/mlb/scoreboard/',
    'cpbl': 'https://tw.sports.yahoo.com/cpbl/scoreboard/',
    'nba':  'https://tw.sports.yahoo.com/nba/scoreboard/',
}

# ─── 快取 ──────────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()
SCOREBOARD_TTL = 600   # 10 分鐘（比賽進行中時更新較快）


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


# ─── 核心爬蟲 ─────────────────────────────────────────────────────

def _fetch_decoded_script(url):
    """取得 Yahoo 頁面並解碼最大的 script 標籤內容"""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if not scripts:
        return ''
    big = max(scripts, key=len)
    # 解碼 Next.js RSC 的 escaped JSON（\"  →  "）
    return big.replace('\\\\\"', '\x00').replace('\\"', '"').replace('\x00', '\\"')


def _parse_game_blocks(decoded, sport_prefix):
    """
    從 decoded script 解析所有比賽 block
    sport_prefix: 'mlb.g.' / 'cpbl.g.' / 'nba.g.'
    回傳 list of game_dict
    """
    games = []
    pattern = rf'"{sport_prefix}(\d+)"'
    starts = [m.start() for m in re.finditer(pattern, decoded)]

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else start + 8000
        block = decoded[start:end]

        # 中文隊名
        away_zh = _re_first(r'"awayTeam":\{[^}]*"displayName":"([^"]+)"', block)
        home_zh = _re_first(r'"homeTeam":\{[^}]*"displayName":"([^"]+)"', block)
        if not away_zh or not home_zh:
            continue

        # 比分
        away_score = _re_int(r'"awayScore":(\d+)', block)
        home_score = _re_int(r'"homeScore":(\d+)', block)

        # 狀態
        status = 'upcoming'
        if away_score is not None and home_score is not None:
            status_raw = _re_first(r'"statusType":"([^"]+)"', block) or ''
            if 'final' in status_raw.lower() or 'completed' in status_raw.lower():
                status = 'finished'
            elif 'in' in status_raw.lower() or 'progress' in status_raw.lower():
                status = 'live'
            else:
                status = 'finished'  # 有比分預設當作結束

        # 天氣（°F 轉 °C）
        temp_f = _re_int(r'"weather":\{"temperature":(\d+)\}', block)
        temp_c = round((temp_f - 32) * 5 / 9, 1) if temp_f is not None else None

        # 球場
        venue = _re_first(r'"venue":\{"displayName":"([^"]+)"\}', block)

        # 勝/敗/救援投手（MLB 賽後才有）
        pitchers = _parse_pitchers(block)

        games.append({
            'away': away_zh,
            'home': home_zh,
            'away_score': away_score,
            'home_score': home_score,
            'status': status,
            'temp_c': temp_c,
            'venue': venue,
            'pitchers': pitchers,
        })

    return games


def _parse_pitchers(block):
    """
    解析 statsLeaders 中的勝/敗/救援投手
    回傳: {'win': {...}, 'loss': {...}, 'save': {...}}
    """
    result = {}
    # 找所有 statsLeaders entries
    for m in re.finditer(r'"playerKey":"(winningPitcher|losingPitcher|savingPitcher)".*?"shortDisplayName":"([^"]+)".*?"stats":\[([^\]]+)\]', block, re.DOTALL):
        key_map = {'winningPitcher': 'win', 'losingPitcher': 'loss', 'savingPitcher': 'save'}
        role = key_map.get(m.group(1), '')
        if not role:
            continue
        name = m.group(2)
        stats_raw = m.group(3)
        stats = re.findall(r'"([^"]+)"', stats_raw)
        result[role] = {'name': name, 'stats': stats}
    return result


def _re_first(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _re_int(pattern, text):
    m = re.search(pattern, text)
    try:
        return int(m.group(1)) if m else None
    except (ValueError, TypeError):
        return None


# ─── 公開查詢函式 ──────────────────────────────────────────────────

def fetch_mlb_games():
    """
    爬取今日 MLB 賽事（Yahoo 台灣）
    回傳: list of game_dict
    """
    cache_key = f'yahoo_mlb_{datetime.now(TW_TZ).strftime("%Y%m%d%H")}'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    games = []
    try:
        decoded = _fetch_decoded_script(SPORT_URLS['mlb'])
        games = _parse_game_blocks(decoded, 'mlb.g.')
        print(f'[YahooData] MLB 爬取 {len(games)} 場')
    except Exception as e:
        print(f'[YahooData] MLB fetch error: {e}')

    _cache_set(cache_key, games, SCOREBOARD_TTL)
    return games


def fetch_cpbl_games():
    """
    爬取今日中職賽事（Yahoo 台灣）
    回傳: list of game_dict
    """
    cache_key = f'yahoo_cpbl_{datetime.now(TW_TZ).strftime("%Y%m%d%H")}'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    games = []
    try:
        decoded = _fetch_decoded_script(SPORT_URLS['cpbl'])
        games = _parse_game_blocks(decoded, 'cpbl.g.')
        print(f'[YahooData] CPBL 爬取 {len(games)} 場')
    except Exception as e:
        print(f'[YahooData] CPBL fetch error: {e}')

    _cache_set(cache_key, games, SCOREBOARD_TTL)
    return games


def get_mlb_game_info(away_zh, home_zh):
    """
    取得特定 MLB 場次的天氣+投手資訊
    回傳: game_dict 或 None
    """
    try:
        games = fetch_mlb_games()
        for g in games:
            if g['away'] == away_zh and g['home'] == home_zh:
                return g
            # 模糊匹配（避免隊名微差）
            if away_zh in g['away'] or g['away'] in away_zh:
                if home_zh in g['home'] or g['home'] in home_zh:
                    return g
        return None
    except Exception:
        return None


def get_cpbl_game_info(away_zh, home_zh):
    """取得特定 CPBL 場次資訊"""
    try:
        games = fetch_cpbl_games()
        for g in games:
            if away_zh in g['away'] or g['away'] in away_zh:
                if home_zh in g['home'] or g['home'] in home_zh:
                    return g
        return None
    except Exception:
        return None


# ─── 分析輔助 ─────────────────────────────────────────────────────

def calc_weather_impact(temp_c):
    """
    依球場溫度計算對大小分的影響分
    正數有利大分，負數有利小分
    MLB 研究：低溫時球飛行距離縮短，高溫時球速略快
    """
    if temp_c is None:
        return 0
    if temp_c < 7:
        return -8    # 極冷 → 強力小分
    elif temp_c < 13:
        return -5    # 偏冷 → 小分
    elif temp_c < 18:
        return -2    # 涼爽 → 略小分
    elif temp_c < 27:
        return 0     # 舒適 → 中性
    elif temp_c < 33:
        return 2     # 偏熱 → 略大分
    else:
        return 4     # 酷熱 → 大分（球場空氣密度低）


def format_weather_text(temp_c, venue=None):
    """格式化天氣顯示文字"""
    if temp_c is None:
        return ''
    impact = calc_weather_impact(temp_c)
    if impact <= -5:
        trend = '❄️ 低溫不利得分，小分機率偏高'
    elif impact <= -2:
        trend = '🌤️ 涼爽氣溫，略偏小分'
    elif impact >= 4:
        trend = '🔥 酷熱氣候，球飛行距離增加，大分機率偏高'
    elif impact >= 2:
        trend = '☀️ 高溫環境，略偏大分'
    else:
        trend = '🌡️ 氣溫適中，對大小分無明顯影響'

    venue_text = f'（{venue}）' if venue else ''
    return f'【球場天氣】{temp_c}°C{venue_text}　{trend}'


def format_pitcher_result(pitchers):
    """格式化賽後投手結果文字"""
    if not pitchers:
        return ''
    parts = []
    if 'win' in pitchers:
        p = pitchers['win']
        stats = ' '.join(p.get('stats', []))
        parts.append(f'勝：{p["name"]}（{stats}）')
    if 'loss' in pitchers:
        p = pitchers['loss']
        stats = ' '.join(p.get('stats', []))
        parts.append(f'敗：{p["name"]}（{stats}）')
    if 'save' in pitchers:
        p = pitchers['save']
        parts.append(f'救援：{p["name"]}')
    return '【投手結果】' + '　'.join(parts) if parts else ''
