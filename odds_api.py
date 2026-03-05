# -*- coding: utf-8 -*-
"""
盤口資料增強模組（可選）
策略：每天自動爬取一次所有賽事盤口 → 存成本地 JSON 檔
後續所有用戶查詢都讀本地檔案，完全不消耗 API 額度。

每日消耗：~5 運動 × 3 額度 = 15 次/天 → 450 次/月（免費 500 內）
資料來源：the-odds-api.com（bet365 / 1xbet 等真實盤口）
"""
import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
ODDS_API_BASE = 'https://api.the-odds-api.com/v4'
ODDS_DATA_DIR = os.path.join(os.path.dirname(__file__), 'odds_data')

SPORT_MAP = {
    'basketball': ['basketball_nba'],
    'baseball': ['baseball_mlb'],
    'soccer': ['soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a',
               'soccer_germany_bundesliga', 'soccer_uefa_champs_league'],
    'hockey': ['icehockey_nhl'],
    'tennis': [],
}

PREFERRED_BOOKMAKERS = ['bet365', '1xbet', 'pinnacle', 'unibet']


# ─── 每日爬取 & 儲存 ────────────────────────────────────────

def _today_str():
    return datetime.now(TW_TZ).strftime('%Y-%m-%d')


def _odds_file(sport):
    """本地盤口 JSON 檔路徑：odds_data/basketball_2026-03-06.json"""
    return os.path.join(ODDS_DATA_DIR, f'{sport}_{_today_str()}.json')


def _is_today_fetched(sport):
    """檢查今天的盤口是否已經爬過"""
    path = _odds_file(sport)
    return os.path.exists(path)


def fetch_and_save(sport='basketball'):
    """
    從 The Odds API 爬取盤口 → 解析 → 存成本地 JSON。
    每天每個運動只需呼叫一次。
    """
    if not ODDS_API_KEY:
        return

    api_sports = SPORT_MAP.get(sport, [])
    if not api_sports:
        return

    all_parsed = []

    for api_sport in api_sports:
        try:
            resp = requests.get(
                f'{ODDS_API_BASE}/sports/{api_sport}/odds/',
                params={
                    'apiKey': ODDS_API_KEY,
                    'regions': 'eu',
                    'markets': 'h2h,spreads,totals',
                    'oddsFormat': 'decimal',
                },
                timeout=20,
            )
            if resp.status_code != 200:
                print(f'[OddsAPI] {api_sport} error: {resp.status_code}')
                continue

            data = resp.json()
            remaining = resp.headers.get('x-requests-remaining', '?')
            print(f'[OddsAPI] {api_sport}: {len(data)} games (quota left: {remaining})')

            # 解析每場比賽
            for gd in data:
                info = _parse_game_odds(gd)
                if info:
                    all_parsed.append(info)

        except Exception as e:
            print(f'[OddsAPI] {api_sport} fetch error: {e}')

    if not all_parsed:
        return

    # 儲存到本地 JSON
    os.makedirs(ODDS_DATA_DIR, exist_ok=True)
    path = _odds_file(sport)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(all_parsed, f, ensure_ascii=False, indent=2)
    print(f'[OddsAPI] Saved {len(all_parsed)} games to {path}')

    # 清理舊檔案（保留最近 3 天）
    _cleanup_old_files()


def _parse_game_odds(gd):
    """解析單場 API 回傳資料，提取最佳博彩公司的盤口"""
    home_en = gd.get('home_team', '')
    away_en = gd.get('away_team', '')
    if not home_en or not away_en:
        return None

    bm = _pick_bookmaker(gd.get('bookmakers', []))
    if not bm:
        return None

    info = {
        'home_team': home_en,
        'away_team': away_en,
        'bookmaker': bm.get('title', ''),
        'commence_time': gd.get('commence_time', ''),
    }

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

    return info


def _cleanup_old_files():
    """刪除 3 天前的盤口檔案"""
    if not os.path.exists(ODDS_DATA_DIR):
        return
    cutoff = (datetime.now(TW_TZ) - timedelta(days=3)).strftime('%Y-%m-%d')
    for f in os.listdir(ODDS_DATA_DIR):
        if f.endswith('.json'):
            # 檔名格式: basketball_2026-03-06.json
            parts = f.replace('.json', '').split('_', 1)
            if len(parts) == 2 and parts[1] < cutoff:
                try:
                    os.remove(os.path.join(ODDS_DATA_DIR, f))
                    print(f'[OddsAPI] Cleaned up old file: {f}')
                except Exception:
                    pass


# ─── 讀取 & 配對 ────────────────────────────────────────

def load_today_odds(sport='basketball'):
    """讀取今天的本地盤口 JSON，回傳 list of dicts"""
    path = _odds_file(sport)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def match_odds_to_games(games, sport='basketball'):
    """
    配對盤口到 playsport 賽事。
    1. 如果今天還沒爬過 → 爬一次並存檔
    2. 讀本地 JSON → 配對到 games
    無 key 時直接返回，不影響任何功能。
    """
    if not ODDS_API_KEY:
        return games

    # 今天還沒爬過 → 自動爬取並儲存
    if not _is_today_fetched(sport):
        fetch_and_save(sport)

    # 讀本地檔案（零 API 消耗）
    odds_list = load_today_odds(sport)
    if not odds_list:
        return games

    # 建立中文→英文反向表
    from scraper import TEAM_NAME_FIX
    cn_to_en = {}
    for k, v in TEAM_NAME_FIX.items():
        if any(c.isascii() and c.isalpha() for c in k):
            cn_to_en[v] = k

    # 配對
    matched = 0
    for game in games:
        home_cn = game.get('home', '')
        away_cn = game.get('away', '')
        home_en = cn_to_en.get(home_cn, '').lower()
        away_en = cn_to_en.get(away_cn, '').lower()

        best = None
        for info in odds_list:
            api_h = info.get('home_team', '').lower()
            api_a = info.get('away_team', '').lower()
            if (home_en and home_en in api_h) and (away_en and away_en in api_a):
                best = info
                break
            if (home_en and home_en in api_h) or (away_en and away_en in api_a):
                best = info
                break

        if best:
            game['odds_api'] = best
            if 'spread_home' in best:
                game['odds']['spread'] = str(best['spread_home'])
                game['odds']['source'] = best.get('bookmaker', '')
            matched += 1

    if matched:
        print(f'[OddsAPI] Matched {matched}/{len(games)} games (from local file)')
    return games


def _pick_bookmaker(bookmakers):
    """按優先順序選博彩公司"""
    for pref in PREFERRED_BOOKMAKERS:
        for bm in bookmakers:
            if bm['key'] == pref:
                return bm
    return bookmakers[0] if bookmakers else None
