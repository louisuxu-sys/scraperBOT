# -*- coding: utf-8 -*-
"""
盤口資料增強模組
從 playsport.cc /guess/ 頁面爬取完整盤口（讓分、大小分、獨贏）。
完全免費，零 API 成本。
"""
import re
import json
import requests
import time

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}

# playsport 競猜頁面聯賽 ID（與 scraper.py 的 PS_LEAGUES 對應）
GUESS_LEAGUE_MAP = {
    'basketball': ['3'],       # NBA
    'baseball': ['1', '114'],  # MLB, WBC
    'soccer': ['4'],           # 足球
    'hockey': ['91'],          # NHL
    'tennis': ['21'],          # 網球
}

# 盤口快取（記憶體內，避免短時間重複爬）
_odds_cache = {}
CACHE_TTL = 300  # 5 分鐘


def fetch_guess_odds(league_id):
    """
    從 playsport.cc /guess/{league_id} 頁面爬取盤口。
    回傳 dict: { gameid: { spread, spreadOdds, total, totalOdds, ml_home, ml_away, ... } }
    """
    cache_key = f'guess_{league_id}'
    now = time.time()
    if cache_key in _odds_cache:
        ts, data = _odds_cache[cache_key]
        if now - ts < CACHE_TTL:
            return data

    url = f'https://www.playsport.cc/guess/{league_id}'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
    except Exception as e:
        print(f'[Odds] fetch guess/{league_id} error: {e}')
        return {}

    # 提取 vueData JSON
    m = re.search(r'var\s+vueData\s*=\s*(\{[\s\S]*?\});\s*(?:</script>|$)', resp.text)
    if not m:
        print(f'[Odds] guess/{league_id}: vueData not found')
        return {}

    try:
        vue_data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f'[Odds] guess/{league_id}: JSON parse error: {e}')
        return {}

    bet_games = vue_data.get('betGamesList', {})
    result = {}

    for date_key, games in bet_games.items():
        for game in games:
            gid = game.get('gameid', '')
            if not gid:
                continue

            home = game.get('home', '')
            away = game.get('away', '')
            gametypes = game.get('gametypes', {})
            info = {
                'home': home,
                'away': away,
                'source': game.get('source', ''),
            }

            # gametype 1: 讓分盤
            gt1 = gametypes.get('1') or gametypes.get(1, {})
            if gt1:
                # 主隊讓分
                home_gt = gt1.get('1') or gt1.get(1, {})
                away_gt = gt1.get('2') or gt1.get(2, {})
                if home_gt:
                    info['spread'] = home_gt.get('threshold', '')
                    info['spread_home_odds'] = home_gt.get('odds', 0)
                if away_gt:
                    info['spread_away_odds'] = away_gt.get('odds', 0)

            # gametype 2: 大小分
            gt2 = gametypes.get('2') or gametypes.get(2, {})
            if gt2:
                over_gt = gt2.get('1') or gt2.get(1, {})
                under_gt = gt2.get('2') or gt2.get(2, {})
                if over_gt:
                    info['total'] = over_gt.get('threshold', '')
                    info['total_over_odds'] = over_gt.get('odds', 0)
                if under_gt:
                    info['total_under_odds'] = under_gt.get('odds', 0)

            # gametype 3: 不讓分（獨贏）
            gt3 = gametypes.get('3') or gametypes.get(3, {})
            if gt3:
                home_ml = gt3.get('1') or gt3.get(1, {})
                away_ml = gt3.get('2') or gt3.get(2, {})
                if home_ml:
                    info['ml_home'] = home_ml.get('odds', 0)
                if away_ml:
                    info['ml_away'] = away_ml.get('odds', 0)

            result[gid] = info

    _odds_cache[cache_key] = (now, result)
    print(f'[Odds] guess/{league_id}: {len(result)} games with odds')
    return result


def match_odds_to_games(games, sport='basketball'):
    """
    從 playsport /guess 頁面取得盤口，配對到已爬取的賽事。
    用 gameid（playsport 的 oid）配對。
    """
    league_ids = GUESS_LEAGUE_MAP.get(sport, [])
    if not league_ids:
        return games

    # 合併所有聯賽的盤口
    all_odds = {}
    for lid in league_ids:
        try:
            odds = fetch_guess_odds(lid)
            all_odds.update(odds)
        except Exception as e:
            print(f'[Odds] guess/{lid} error: {e}')

    if not all_odds:
        return games

    # 配對：用 oid 中的 gameid 部分
    matched = 0
    for game in games:
        oid = game.get('oid', '')
        home_name = game.get('home', '')
        away_name = game.get('away', '')

        # 嘗試直接用 oid 配對
        best = all_odds.get(oid)

        # 備援：用隊名模糊配對
        if not best:
            for gid, info in all_odds.items():
                oh = info.get('home', '')
                oa = info.get('away', '')
                if oh and oa and (home_name in oh or oh in home_name) and \
                   (away_name in oa or oa in away_name):
                    best = info
                    break

        if best:
            game['odds_api'] = best
            # 同時更新基本 spread（相容現有邏輯）
            if best.get('spread'):
                game['odds']['spread'] = best['spread']
                game['odds']['source'] = best.get('source', 'playsport')
            matched += 1

    if matched:
        print(f'[Odds] Matched {matched}/{len(games)} games')
    return games
