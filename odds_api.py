# -*- coding: utf-8 -*-
"""
盤口資料增強模組
結合 playsport.cc 三個頁面爬取完整盤口資訊：
  1. /predict/games — 運彩盤讓分、大小分、獨贏 + 賠率
  2. /predict/scale — 玩家預測比例、走勢方向
  3. /guess/{id}    — 國際盤讓分、大小分、獨贏（含賠率）
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

# predict/games + predict/scale 支援的聯賽 ID
PREDICT_LEAGUE_MAP = {
    'basketball': ['3', '8', '92'],     # NBA, 歐洲職籃, 韓國職籃
    'baseball': ['1', '2', '6', '9'],   # MLB, 日職, 中職, 韓職
    'soccer': ['4'],                     # 足球
    'hockey': ['91'],                    # NHL
    'tennis': ['21'],                    # 網球
}

# guess 頁面支援的聯賽 ID（含特殊聯賽如 WBC）
GUESS_LEAGUE_MAP = {
    'basketball': ['3', '8', '92', '97'],     # NBA, 歐洲職籃, 韓國職籃, 日本職籃
    'baseball': ['1', '2', '6', '9', '114'],  # MLB, 日職, 中職, 韓職, WBC
    'soccer': ['4'],                           # 足球
    'hockey': ['91'],                          # NHL
    'tennis': ['21'],                          # 網球
}

# 盤口快取
_odds_cache = {}
CACHE_TTL = 300  # 5 分鐘


def _strip_tags(s):
    return re.sub(r'<[^>]+>', '', s).strip()


def _get_cached(key):
    if key in _odds_cache:
        ts, data = _odds_cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _set_cache(key, data):
    _odds_cache[key] = (time.time(), data)


def _safe_get(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        return resp.text
    except Exception as e:
        print(f'[Odds] fetch {url} error: {e}')
        return None


def _parse_bet_cell(row_html, bet_class):
    """解析 predict/games 的 data-wrap: <strong>-6.5</strong><span>, 1.68</span>"""
    cell_m = re.search(rf'td-bank-{bet_class}([\s\S]*?)(?:</td>)', row_html)
    if not cell_m:
        return None, None
    cell = cell_m.group(1)
    val_m = re.search(r'data-wrap[^>]*>[\s\S]*?<strong>(.*?)</strong>', cell)
    odds_m = re.search(r'data-wrap[^>]*>[\s\S]*?<span>(.*?)</span>', cell)
    val = _strip_tags(val_m.group(1)) if val_m else None
    odds_str = _strip_tags(odds_m.group(1)).lstrip(',').strip() if odds_m else None
    return val, odds_str


def _parse_rows(html):
    """解析 predict 頁面的 gameid → (away_row, home_row) 對"""
    gameids = list(dict.fromkeys(re.findall(r'gameid="(\d+)"', html)))
    result = {}
    for gid in gameids:
        rows = list(re.finditer(
            rf'<tr[^>]*gameid="{gid}"[^>]*>([\s\S]*?)</tr>', html
        ))
        if len(rows) >= 2:
            result[gid] = (rows[0].group(1), rows[1].group(1))
    return result


def _parse_team_name(row_html):
    m = re.search(r'td-teaminfo[\s\S]*?<h3[^>]*>([\s\S]*?)</h3>', row_html)
    return _strip_tags(m.group(1)) if m else ''


# ─── 1. /predict/games：運彩盤口 + 賠率 ───
def fetch_predict_games(league_id):
    cache_key = f'games_{league_id}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    html = _safe_get(f'https://www.playsport.cc/predict/games?allianceid={league_id}&type=p')
    if not html:
        return []

    rows_map = _parse_rows(html)
    result = []

    for gid, (away_row, home_row) in rows_map.items():
        away = _parse_team_name(away_row)
        home = _parse_team_name(home_row)
        if not away or not home:
            continue

        info = {'gameid': gid, 'home': home, 'away': away, 'source': 'playsport'}

        # 讓分 (bet01)
        h_spread, h_sp_odds = _parse_bet_cell(home_row, 'bet01')
        a_spread, a_sp_odds = _parse_bet_cell(away_row, 'bet01')
        if h_spread:
            info['spread'] = h_spread
            info['spread_home_odds'] = h_sp_odds
            info['spread_away_odds'] = a_sp_odds

        # 大小分 (bet02)
        total_val, over_odds = _parse_bet_cell(away_row, 'bet02')
        _, under_odds = _parse_bet_cell(home_row, 'bet02')
        if total_val:
            info['total'] = total_val
            info['total_over_odds'] = over_odds
            info['total_under_odds'] = under_odds

        # 獨贏 (bet03)
        _, ml_home = _parse_bet_cell(home_row, 'bet03')
        _, ml_away = _parse_bet_cell(away_row, 'bet03')
        if ml_home:
            info['ml_home'] = ml_home
        if ml_away:
            info['ml_away'] = ml_away

        result.append(info)

    _set_cache(cache_key, result)
    if result:
        print(f'[Odds] predict/games/{league_id}: {len(result)} games')
    return result


# ─── 2. /predict/scale：玩家預測比例 + 走勢 ───
def fetch_predict_scale(league_id):
    cache_key = f'scale_{league_id}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    html = _safe_get(f'https://www.playsport.cc/predict/scale?allianceid={league_id}')
    if not html:
        return {}

    rows_map = _parse_rows(html)
    result = {}

    for gid, (away_row, home_row) in rows_map.items():
        away = _parse_team_name(away_row)
        home = _parse_team_name(home_row)
        if not away or not home:
            continue

        info = {'home': home, 'away': away}

        # 走勢: data-wrap 中 <strong>15分贏</strong><span>50%</span>
        def parse_trend(row_html):
            cell = re.search(r'td-universal-bet01([\s\S]*?)(?:</td>)', row_html)
            if not cell:
                return None
            c = cell.group(1)
            val_m = re.search(r'data-wrap[^>]*>[\s\S]*?<strong>(.*?)</strong>', c)
            return _strip_tags(val_m.group(1)) if val_m else None

        # 預測比例
        def parse_predict_pct(row_html):
            m = re.search(r'predict-s\s*"[\s\S]*?<b>(.*?)</b>', row_html)
            return m.group(1).strip() if m else None

        # 預測人數
        def parse_count(row_html):
            m = re.search(r'(\d+)\s*人預測', row_html)
            return int(m.group(1)) if m else None

        home_trend = parse_trend(home_row)
        if home_trend:
            info['home_trend'] = home_trend

        home_pct = parse_predict_pct(home_row)
        away_pct = parse_predict_pct(away_row)
        if home_pct:
            info['home_predict_pct'] = home_pct
        if away_pct:
            info['away_predict_pct'] = away_pct

        count = parse_count(home_row) or parse_count(away_row)
        if count:
            info['predict_count'] = count

        result[gid] = info

    _set_cache(cache_key, result)
    if result:
        print(f'[Odds] predict/scale/{league_id}: {len(result)} games')
    return result


# ─── 3. /guess/{id}：國際盤（讓分、大小分、獨贏）+ 賠率 ───
def fetch_guess_odds(league_id):
    cache_key = f'guess_{league_id}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    html = _safe_get(f'https://www.playsport.cc/guess/{league_id}')
    if not html:
        return {}

    m = re.search(r'var\s+vueData\s*=\s*(\{[\s\S]*?\});\s*(?:</script>|$)', html)
    if not m:
        return {}

    try:
        vue_data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}

    bet_games = vue_data.get('betGamesList', {})
    result = {}

    # betGamesList 可能是 dict（按日期分組）或 list
    if isinstance(bet_games, list):
        all_games = bet_games
    elif isinstance(bet_games, dict):
        all_games = []
        for games in bet_games.values():
            if isinstance(games, list):
                all_games.extend(games)
    else:
        all_games = []

    for game in all_games:
        gid = game.get('gameid', '')
        if not gid:
            continue

        home = game.get('home', '')
        away = game.get('away', '')
        gt = game.get('gametypes', {})
        info = {'home': home, 'away': away}

        # gametype 1=國際讓分, 2=國際大小, 3=獨贏, 5=運彩讓分, 6=運彩大小
        for key, label, fields in [
            ('1', 'intl_spread', ['threshold', 'odds']),
            ('5', 'tw_spread', ['threshold', 'odds']),
            ('3', 'ml', ['odds']),
            ('2', 'intl_total', ['threshold', 'odds']),
            ('6', 'tw_total', ['threshold', 'odds']),
        ]:
            gt_data = gt.get(key, {})
            if not gt_data:
                continue
            t1 = gt_data.get('1', {})
            t2 = gt_data.get('2', {})
            if label == 'ml':
                if t1.get('odds'):
                    info['guess_ml_home'] = t1['odds']
                if t2.get('odds'):
                    info['guess_ml_away'] = t2['odds']
            elif 'spread' in label:
                if t1.get('threshold'):
                    info[f'{label}_home'] = t1['threshold']
                    info[f'{label}_home_odds'] = t1.get('odds', '')
                if t2.get('threshold'):
                    info[f'{label}_away'] = t2['threshold']
                    info[f'{label}_away_odds'] = t2.get('odds', '')
            elif 'total' in label:
                if t1.get('threshold'):
                    info[f'{label}_line'] = t1['threshold']
                    info[f'{label}_over_odds'] = t1.get('odds', '')
                    info[f'{label}_under_odds'] = t2.get('odds', '')

        result[gid] = info

    _set_cache(cache_key, result)
    if result:
        print(f'[Odds] guess/{league_id}: {len(result)} games')
    return result


# ─── 隊名模糊配對 ───
def _name_match(name1, name2):
    """兩個隊名是否匹配（支援截斷/全名）"""
    if not name1 or not name2:
        return False
    return name1 == name2 or name1 in name2 or name2 in name1


# ─── 主要入口：合併三個來源，配對到賽事 ───
def match_odds_to_games(games, sport='basketball'):
    """
    結合 predict/games + predict/scale + guess 三頁面盤口，用隊名配對。
    predict/games 和 predict/scale 只支援主要聯賽。
    guess 支援所有聯賽（含 WBC 等特殊聯賽）。
    """
    predict_ids = PREDICT_LEAGUE_MAP.get(sport, [])
    guess_ids = GUESS_LEAGUE_MAP.get(sport, [])

    if not predict_ids and not guess_ids:
        return games

    # 收集所有來源
    all_games_odds = []  # predict/games 列表
    all_scale = {}       # predict/scale gameid → dict
    all_guess = {}       # guess gameid → dict

    for lid in predict_ids:
        try:
            all_games_odds.extend(fetch_predict_games(lid))
        except Exception as e:
            print(f'[Odds] predict/games/{lid} error: {e}')
        try:
            all_scale.update(fetch_predict_scale(lid))
        except Exception as e:
            print(f'[Odds] predict/scale/{lid} error: {e}')

    for lid in guess_ids:
        try:
            all_guess.update(fetch_guess_odds(lid))
        except Exception as e:
            print(f'[Odds] guess/{lid} error: {e}')

    if not all_games_odds and not all_guess:
        return games

    # 用隊名配對
    matched = 0
    for game in games:
        home_name = game.get('home', '')
        away_name = game.get('away', '')
        if not home_name or not away_name:
            continue

        best = None

        # 優先從 predict/games 配對（有運彩盤口+賠率）
        for info in all_games_odds:
            if _name_match(home_name, info.get('home', '')) and \
               _name_match(away_name, info.get('away', '')):
                best = dict(info)
                break

        # 合併 scale 資料（用 gameid）
        if best:
            gid = best.get('gameid', '')
            if gid and gid in all_scale:
                scale = all_scale[gid]
                for k in ('home_trend', 'home_predict_pct', 'away_predict_pct', 'predict_count'):
                    if scale.get(k):
                        best[k] = scale[k]

        # 合併或建立 guess 國際盤資料
        for guess_gid, guess_info in all_guess.items():
            gh = guess_info.get('home', '')
            ga = guess_info.get('away', '')
            if _name_match(home_name, gh) and _name_match(away_name, ga):
                if best is None:
                    # 沒有 predict 資料，用 guess 建立基礎
                    best = {'home': home_name, 'away': away_name, 'source': 'playsport'}
                # 合併 guess 資料
                for k, v in guess_info.items():
                    if k not in ('home', 'away') and v:
                        best[k] = v
                break

        if best:
            game['odds_api'] = best
            # 相容現有邏輯：優先用運彩盤讓分，無則用國際盤
            spread = best.get('spread') or best.get('intl_spread_home')
            if spread:
                game['odds']['spread'] = spread
                game['odds']['source'] = 'playsport'
            matched += 1

    if matched:
        print(f'[Odds] Matched {matched}/{len(games)} games')
    return games
