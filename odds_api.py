# -*- coding: utf-8 -*-
"""
盤口資料增強模組（可選）
如有設定 ODDS_API_KEY 環境變數，會從 the-odds-api.com 取得
bet365/1xbet 等博彩公司的真實盤口（讓分、獨贏、大小分）。
未設定時完全靜默，不影響任何功能。
免費方案：500 requests/month → https://the-odds-api.com/account/
"""
import os
import time
import requests

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
ODDS_API_BASE = 'https://api.the-odds-api.com/v4'

SPORT_MAP = {
    'basketball': ['basketball_nba'],
    'baseball': ['baseball_mlb'],
    'soccer': ['soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a',
               'soccer_germany_bundesliga', 'soccer_uefa_champs_league'],
    'hockey': ['icehockey_nhl'],
    'tennis': [],
}

PREFERRED_BOOKMAKERS = ['bet365', '1xbet', 'pinnacle', 'unibet']

_odds_cache = {}
CACHE_TTL = 600


def match_odds_to_games(games, sport='basketball'):
    """配對 The Odds API 盤口到 playsport 賽事。無 key 時直接返回。"""
    if not ODDS_API_KEY:
        return games

    api_sports = SPORT_MAP.get(sport, [])
    if not api_sports:
        return games

    # 取得盤口（h2h + spreads + totals 合併一次請求）
    all_odds = []
    for api_sport in api_sports:
        cache_key = f'{api_sport}_all'
        now = time.time()
        if cache_key in _odds_cache:
            ts, data = _odds_cache[cache_key]
            if now - ts < CACHE_TTL:
                all_odds.extend(data)
                continue
        try:
            resp = requests.get(
                f'{ODDS_API_BASE}/sports/{api_sport}/odds/',
                params={
                    'apiKey': ODDS_API_KEY,
                    'regions': 'eu',
                    'markets': 'h2h,spreads,totals',
                    'oddsFormat': 'decimal',
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            _odds_cache[cache_key] = (now, data)
            all_odds.extend(data)
            remaining = resp.headers.get('x-requests-remaining', '?')
            print(f'[OddsAPI] {api_sport}: {len(data)} games (quota left: {remaining})')
        except Exception:
            pass

    if not all_odds:
        return games

    # 建立英文隊名 → 盤口 dict
    from scraper import TEAM_NAME_FIX
    # 反向表：中文→英文
    cn_to_en = {}
    for k, v in TEAM_NAME_FIX.items():
        if any(c.isascii() and c.isalpha() for c in k):
            cn_to_en[v] = k

    # 解析每場 API 資料
    api_games = {}
    for gd in all_odds:
        home_en = gd.get('home_team', '')
        away_en = gd.get('away_team', '')
        info = {'home_team': home_en, 'away_team': away_en}

        # 找最佳博彩公司
        bm = _pick_bookmaker(gd.get('bookmakers', []))
        if not bm:
            continue
        info['bookmaker'] = bm.get('title', '')

        for mkt in bm.get('markets', []):
            if mkt['key'] == 'h2h':
                for o in mkt['outcomes']:
                    if o['name'] == home_en:
                        info['h2h_home'] = o['price']
                    elif o['name'] == away_en:
                        info['h2h_away'] = o['price']
                    elif o['name'] == 'Draw':
                        info['h2h_draw'] = o['price']
            elif mkt['key'] == 'spreads':
                for o in mkt['outcomes']:
                    if o['name'] == home_en:
                        info['spread_home'] = o.get('point', 0)
                    elif o['name'] == away_en:
                        info['spread_away'] = o.get('point', 0)
            elif mkt['key'] == 'totals':
                for o in mkt['outcomes']:
                    if o['name'] == 'Over':
                        info['total_over'] = o.get('point', 0)
                    elif o['name'] == 'Under':
                        info['total_under'] = o.get('point', 0)

        api_games[(home_en.lower(), away_en.lower())] = info

    # 配對：中文隊名 ↔ 英文隊名
    matched = 0
    for game in games:
        home_cn = game.get('home', '')
        away_cn = game.get('away', '')
        home_en = cn_to_en.get(home_cn, '').lower()
        away_en = cn_to_en.get(away_cn, '').lower()

        best = None
        for (ah, aa), info in api_games.items():
            if (home_en and home_en in ah) or (away_en and away_en in aa):
                best = info
                break
            if (home_en and home_en in aa) or (away_en and away_en in ah):
                best = info
                break

        if best:
            game['odds_api'] = best
            if 'spread_home' in best:
                game['odds']['spread'] = str(best['spread_home'])
                game['odds']['source'] = best.get('bookmaker', '')
            matched += 1

    if matched:
        print(f'[OddsAPI] Matched {matched}/{len(games)} games')
    return games


def _pick_bookmaker(bookmakers):
    """按優先順序選博彩公司"""
    for pref in PREFERRED_BOOKMAKERS:
        for bm in bookmakers:
            if bm['key'] == pref:
                return bm
    return bookmakers[0] if bookmakers else None
