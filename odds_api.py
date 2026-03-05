# -*- coding: utf-8 -*-
"""
The Odds API 盤口資料模組
從 the-odds-api.com 取得 bet365/1xbet 等博彩公司的盤口數據
免費方案：500 requests/month
"""
import os
import time
import requests
from datetime import datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
ODDS_API_BASE = 'https://api.the-odds-api.com/v4'

# 運動對照：我們的 sport key → The Odds API sport key
SPORT_MAP = {
    'basketball': ['basketball_nba'],
    'baseball': ['baseball_mlb'],
    'soccer': ['soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a',
               'soccer_germany_bundesliga', 'soccer_france_ligue_one',
               'soccer_uefa_champs_league'],
    'hockey': ['icehockey_nhl'],
    'tennis': [],  # 網球聯賽太多，暫不支援
}

# 優先使用的博彩公司（按優先順序）
PREFERRED_BOOKMAKERS = ['bet365', '1xbet', 'pinnacle', 'unibet', 'williamhill']

# 快取：避免重複呼叫 API
_odds_cache = {}
CACHE_TTL = 600  # 10 分鐘


def fetch_odds(sport='basketball', market='spreads'):
    """
    從 The Odds API 取得盤口
    sport: 'basketball', 'baseball', 'soccer', 'hockey'
    market: 'h2h' (獨贏), 'spreads' (讓分), 'totals' (大小分)
    回傳: list of game odds dicts
    """
    if not ODDS_API_KEY:
        print('[OddsAPI] No API key configured')
        return []

    api_sports = SPORT_MAP.get(sport, [])
    if not api_sports:
        return []

    all_odds = []
    for api_sport in api_sports:
        cache_key = f'{api_sport}_{market}'
        now = time.time()

        # 檢查快取
        if cache_key in _odds_cache:
            cached_time, cached_data = _odds_cache[cache_key]
            if now - cached_time < CACHE_TTL:
                all_odds.extend(cached_data)
                continue

        url = f'{ODDS_API_BASE}/sports/{api_sport}/odds/'
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': 'eu',  # 歐洲區包含 bet365, 1xbet
            'markets': market,
            'oddsFormat': 'decimal',
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 401:
                print('[OddsAPI] Invalid API key')
                return []
            if resp.status_code == 429:
                print('[OddsAPI] Rate limited')
                return []
            if resp.status_code != 200:
                print(f'[OddsAPI] Error {resp.status_code}: {resp.text[:200]}')
                return []

            data = resp.json()
            remaining = resp.headers.get('x-requests-remaining', '?')
            print(f'[OddsAPI] {api_sport}/{market}: {len(data)} games (remaining: {remaining})')

            _odds_cache[cache_key] = (now, data)
            all_odds.extend(data)

        except Exception as e:
            print(f'[OddsAPI] Error: {e}')

    return all_odds


def extract_best_odds(game_odds, market='spreads'):
    """
    從 API 回傳的單場比賽盤口中，提取最佳博彩公司的盤口
    game_odds: The Odds API 回傳的單場 game dict
    market: 'h2h', 'spreads', 'totals'
    回傳: dict with odds info, or None
    """
    bookmakers = game_odds.get('bookmakers', [])
    if not bookmakers:
        return None

    # 按優先順序找博彩公司
    selected = None
    for pref in PREFERRED_BOOKMAKERS:
        for bm in bookmakers:
            if bm['key'] == pref:
                selected = bm
                break
        if selected:
            break

    # 如果沒找到優先的，用第一個
    if not selected:
        selected = bookmakers[0]

    result = {
        'bookmaker': selected.get('title', selected.get('key', '')),
        'last_update': selected.get('last_update', ''),
        'home_team': game_odds.get('home_team', ''),
        'away_team': game_odds.get('away_team', ''),
    }

    for mkt in selected.get('markets', []):
        if mkt['key'] == 'h2h':
            outcomes = {o['name']: o['price'] for o in mkt.get('outcomes', [])}
            result['h2h_home'] = outcomes.get(game_odds.get('home_team'), 0)
            result['h2h_away'] = outcomes.get(game_odds.get('away_team'), 0)
            draw_price = outcomes.get('Draw')
            if draw_price:
                result['h2h_draw'] = draw_price

        elif mkt['key'] == 'spreads':
            for o in mkt.get('outcomes', []):
                if o['name'] == game_odds.get('home_team'):
                    result['spread_home'] = o.get('point', 0)
                    result['spread_home_price'] = o.get('price', 0)
                elif o['name'] == game_odds.get('away_team'):
                    result['spread_away'] = o.get('point', 0)
                    result['spread_away_price'] = o.get('price', 0)

        elif mkt['key'] == 'totals':
            for o in mkt.get('outcomes', []):
                if o['name'] == 'Over':
                    result['total_over'] = o.get('point', 0)
                    result['total_over_price'] = o.get('price', 0)
                elif o['name'] == 'Under':
                    result['total_under'] = o.get('point', 0)
                    result['total_under_price'] = o.get('price', 0)

    return result


def match_odds_to_games(games, sport='basketball'):
    """
    將 The Odds API 的盤口配對到 playsport 的賽事列表
    使用隊名模糊匹配
    games: playsport 的 game list
    sport: 運動類型
    回傳: 修改後的 games（新增 odds_api 欄位）
    """
    if not ODDS_API_KEY:
        return games

    # 取得所有市場的盤口
    h2h_data = fetch_odds(sport, 'h2h')
    spreads_data = fetch_odds(sport, 'spreads')
    totals_data = fetch_odds(sport, 'totals')

    # 建立隊名對照（英文→盤口資料）
    odds_by_teams = {}  # key: (home_lower, away_lower) → merged odds

    for dataset, market in [(h2h_data, 'h2h'), (spreads_data, 'spreads'), (totals_data, 'totals')]:
        for gd in dataset:
            home = gd.get('home_team', '').lower()
            away = gd.get('away_team', '').lower()
            key = (home, away)

            if key not in odds_by_teams:
                odds_by_teams[key] = {
                    'home_team': gd.get('home_team', ''),
                    'away_team': gd.get('away_team', ''),
                }

            extracted = extract_best_odds(gd, market)
            if extracted:
                odds_by_teams[key].update(extracted)

    # 隊名匹配：中文隊名 → 英文隊名
    # 反向查表：用 TEAM_NAME_FIX 的英文→中文對照建立中文→英文
    from scraper import TEAM_NAME_FIX
    cn_to_en = {}
    for en_or_cn, cn in TEAM_NAME_FIX.items():
        # 如果 key 是英文（含大寫字母），建立反向映射
        if any(c.isascii() and c.isalpha() for c in en_or_cn):
            cn_to_en[cn] = en_or_cn
        # 也把中文名本身加入
        cn_to_en[cn] = cn

    # 配對
    matched = 0
    for game in games:
        home_cn = game.get('home', '')
        away_cn = game.get('away', '')

        # 嘗試直接用中文匹配（不太可能成功）
        # 嘗試找到英文名
        best_match = None
        best_score = 0

        for (api_home, api_away), odds_info in odds_by_teams.items():
            score = 0
            api_home_full = odds_info.get('home_team', '')
            api_away_full = odds_info.get('away_team', '')

            # 方法1：精確中文匹配（如果 API 回傳中文名）
            if home_cn.lower() == api_home or away_cn.lower() == api_away:
                score = 10

            # 方法2：透過 TEAM_NAME_FIX 反查英文名
            for cn_name, api_name in [(home_cn, api_home_full), (away_cn, api_away_full)]:
                # 檢查中文名是否在英文名內或反之
                en_name = cn_to_en.get(cn_name, '')
                if en_name and en_name.lower() in api_name.lower():
                    score += 5
                # 直接包含匹配
                if cn_name in api_name or api_name in cn_name:
                    score += 3

            if score > best_score:
                best_score = score
                best_match = odds_info

        if best_match and best_score >= 5:
            game['odds_api'] = best_match
            matched += 1

            # 更新 game 的 odds 字典（讓分盤）
            if 'spread_home' in best_match:
                game['odds']['spread'] = str(best_match['spread_home'])
                game['odds']['source'] = best_match.get('bookmaker', '')

    print(f'[OddsAPI] Matched {matched}/{len(games)} games for {sport}')
    return games


if __name__ == '__main__':
    # 測試
    if ODDS_API_KEY:
        print('Testing The Odds API...')
        odds = fetch_odds('basketball', 'spreads')
        print(f'Got {len(odds)} NBA games')
        if odds:
            best = extract_best_odds(odds[0], 'spreads')
            print(f'First game: {best}')
    else:
        print('Set ODDS_API_KEY environment variable to test')
        print('Get free key at: https://the-odds-api.com/account/')
