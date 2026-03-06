# -*- coding: utf-8 -*-
"""
盤口資料增強模組
從 playsport.cc /predict/games 頁面爬取完整盤口（讓分、大小分、獨贏）。
完全免費，零 API 成本。
"""
import re
import requests
import time

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}

# playsport 預測頁面聯賽 ID（與 scraper.py 的 PS_LEAGUES 對應）
PREDICT_LEAGUE_MAP = {
    'basketball': ['3', '8', '92', '97'],   # NBA, 歐洲職籃, 韓國職籃, 日本職籃
    'baseball': ['1', '2', '6', '9', '114'],  # MLB, 日職, 中職, 韓職, WBC
    'soccer': ['4'],                          # 足球
    'hockey': ['91'],                         # NHL
    'tennis': ['21'],                         # 網球
}

# 盤口快取（記憶體內，避免短時間重複爬）
_odds_cache = {}
CACHE_TTL = 300  # 5 分鐘


def fetch_predict_odds(league_id):
    """
    從 playsport.cc /predict/games 頁面爬取盤口。
    回傳 list of dicts: [{ home, away, spread, ml_home, ml_away, total, ... }]
    """
    cache_key = f'predict_{league_id}'
    now = time.time()
    if cache_key in _odds_cache:
        ts, data = _odds_cache[cache_key]
        if now - ts < CACHE_TTL:
            return data

    url = f'https://www.playsport.cc/predict/games?allianceid={league_id}&type=p'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
    except Exception as e:
        print(f'[Odds] fetch predict/{league_id} error: {e}')
        return []

    html = resp.text
    result = []

    # 找所有 gameid（去重保持順序）
    gameids = list(dict.fromkeys(re.findall(r'gameid="(\d+)"', html)))

    for gid in gameids:
        # 找該 gameid 的兩個 <tr> 行
        rows = list(re.finditer(
            rf'<tr[^>]*gameid="{gid}"[^>]*>([\s\S]*?)</tr>', html
        ))
        if len(rows) < 2:
            continue

        away_row = rows[0].group(1)  # 第一行 = 客隊
        home_row = rows[1].group(1)  # 第二行 = 主隊

        # 隊名
        away_m = re.search(
            r'td-teaminfo[\s\S]*?<h3[^>]*>\s*(?:<a[^>]*>)?(.*?)(?:</a>)?\s*</h3>',
            away_row
        )
        home_m = re.search(
            r'td-teaminfo[\s\S]*?<h3[^>]*>\s*(?:<a[^>]*>)?(.*?)(?:</a>)?\s*</h3>',
            home_row
        )
        away = away_m.group(1).strip() if away_m else ''
        home = home_m.group(1).strip() if home_m else ''
        if not away or not home:
            continue

        info = {'home': home, 'away': away, 'source': 'playsport'}

        # 運彩盤讓分 (td-bank-bet01) — 主隊行
        spread_m = re.search(
            r'td-bank-bet01[\s\S]*?<strong>([+-]?\d+\.?\d*)</strong>',
            home_row
        )
        if spread_m:
            info['spread'] = spread_m.group(1)

        # 不讓分賠率 (td-bank-bet03)
        ml_home_m = re.search(
            r'td-bank-bet03[\s\S]*?<span>(\d+\.?\d*)</span>',
            home_row
        )
        ml_away_m = re.search(
            r'td-bank-bet03[\s\S]*?<span>(\d+\.?\d*)</span>',
            away_row
        )
        if ml_home_m:
            info['ml_home'] = ml_home_m.group(1)
        if ml_away_m:
            info['ml_away'] = ml_away_m.group(1)

        # 大小分 (td-bank-bet02) — 客隊行取大分線
        total_m = re.search(
            r'td-bank-bet02[\s\S]*?<strong>(\d+\.?\d*)</strong>',
            away_row
        )
        if total_m:
            info['total'] = total_m.group(1)

        result.append(info)

    _odds_cache[cache_key] = (now, result)
    if result:
        print(f'[Odds] predict/{league_id}: {len(result)} games with odds')
    return result


def match_odds_to_games(games, sport='basketball'):
    """
    從 playsport /predict/games 頁面取得盤口，用隊名配對到已爬取的賽事。
    """
    league_ids = PREDICT_LEAGUE_MAP.get(sport, [])
    if not league_ids:
        return games

    # 合併所有聯賽的盤口
    all_odds = []
    for lid in league_ids:
        try:
            odds = fetch_predict_odds(lid)
            all_odds.extend(odds)
        except Exception as e:
            print(f'[Odds] predict/{lid} error: {e}')

    if not all_odds:
        return games

    # 用隊名配對
    matched = 0
    for game in games:
        home_name = game.get('home', '')
        away_name = game.get('away', '')
        if not home_name or not away_name:
            continue

        best = None
        for info in all_odds:
            oh = info.get('home', '')
            oa = info.get('away', '')
            # 精確配對（含子字串相容截斷隊名）
            if oh and oa and \
               (home_name == oh or home_name in oh or oh in home_name) and \
               (away_name == oa or away_name in oa or oa in away_name):
                best = info
                break

        if best:
            game['odds_api'] = best
            # 同時更新基本 spread（相容現有邏輯）
            if best.get('spread'):
                game['odds']['spread'] = best['spread']
                game['odds']['source'] = 'playsport'
            matched += 1

    if matched:
        print(f'[Odds] Matched {matched}/{len(games)} games')
    return games
