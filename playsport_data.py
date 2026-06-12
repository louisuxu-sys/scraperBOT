# -*- coding: utf-8 -*-
"""
playsport.cc 賽事資料整合模組
爬取對戰資訊（battle）、賽事結果（result）頁面，
提供實際賠率、過盤率、戰績等數據，用於更精確的 EV 計算。
"""
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}

# ─── 快取 ───
_cache = {}
CACHE_TTL = 300  # 5 分鐘

SPORT_ALLIANCE = {
    'basketball': '3',
    'baseball': '1',
    'soccer': '4',     # 世足賽 / 足球
}

# 足球 predict 頁面用的 alliance ID
SOCCER_PREDICT_ALLIANCES = ['4', '1']  # 4=世足賽, 1=世界盃(開賽後)


def _safe_get(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = 'utf-8'
        return resp.text
    except Exception as e:
        print(f'[PlaysportData] fetch error: {e}')
        return None


def _get_cached(key):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _set_cached(key, data):
    _cache[key] = (time.time(), data)


# ═══════════════════════════════════════════════════════
#  1. 對戰資訊頁面（battle）— 取得實際盤口賠率
# ═══════════════════════════════════════════════════════

def build_gameid(game):
    """從 game dict 建構 playsport gameid"""
    game_id = game.get('gameId', '')
    date_str = game.get('date', '')
    if not game_id or not date_str or game_id.startswith('sel_'):
        return None
    date_clean = date_str.replace('-', '')
    return f'{date_clean}{game_id}'


def fetch_battle_odds(game):
    """
    從對戰資訊頁面取得實際賠率。
    回傳 dict:
    {
        'tw_spread_home': float,    # 運彩讓分值（主隊視角，負=主讓）
        'tw_spread_odds': float,    # 運彩讓分賠率
        'tw_total_line': float,     # 運彩大小分盤口線
        'tw_over_odds': float,      # 運彩大分賠率
        'tw_under_odds': float,     # 運彩小分賠率
        'tw_ml_home': float,        # 運彩主隊不讓分賠率
        'tw_ml_away': float,        # 運彩客隊不讓分賠率
        'intl_spread_result': str,  # 國際讓分結果（贏/輸）
        'intl_ou_result': str,      # 國際大小結果（大/小）
        'intl_ou_pct': str,         # 國際大小預測比例
        'h2h_spread_pct': str,      # 對戰讓分過盤率
        'h2h_over_pct': str,        # 對戰大分過盤率
        'season_spread_pct': dict,  # 本季讓分過盤率 {away, home}
        'season_over_pct': dict,    # 本季大分過盤率 {away, home}
    }
    """
    gameid = build_gameid(game)
    if not gameid:
        return None

    cache_key = f'battle_{gameid}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    url = f'https://www.playsport.cc/gamesData/battle?gameid={gameid}'
    html = _safe_get(url)
    if not html:
        return None

    result = _parse_battle_html(html)
    _set_cached(cache_key, result)
    return result


def _extract_td_cells(row_html):
    """從一行 <tr> 中提取所有 <td> 及其 class 與內容"""
    cells = {}
    for m in re.finditer(r'<td\s+class="([^"]*)"[^>]*>([\s\S]*?)</td>', row_html):
        cls = m.group(1)
        content = m.group(2)
        # 用主要 class 名作為 key（取第一個 td- 開頭的）
        key_m = re.search(r'(td-[\w-]+)', cls)
        if key_m:
            cells[key_m.group(1)] = content
    return cells


def _parse_cell_odds(cell_html):
    """從單一 <td> 內容中提取 <strong> 值和賠率"""
    strongs = re.findall(r'<strong[^>]*>([^<]*)</strong>', cell_html)
    odds_m = re.search(r'<span[^>]*>,?\s*([\d.]+)', cell_html)
    odds_val = float(odds_m.group(1)) if odds_m else None
    return strongs, odds_val


def _parse_battle_html(html):
    """解析對戰資訊頁面 HTML"""
    data = {}

    # ── 對戰盤口區塊 ──
    odds_block = re.search(r'對戰盤口([\s\S]*?)anchorLast10Records', html)
    if odds_block:
        block = odds_block.group(1)
        rows = list(re.finditer(r'<tr[^>]*>([\s\S]*?)</tr>', block))

        # 第一隊（客隊）row[2]，第二隊（主隊）row[3]
        if len(rows) >= 4:
            away_cells = _extract_td_cells(rows[2].group(1))
            home_cells = _extract_td_cells(rows[3].group(1))

            # 運彩讓分（td-bank-bet01）
            for label, cells in [('away', away_cells), ('home', home_cells)]:
                cell = cells.get('td-bank-bet01', '')
                if cell.strip():
                    strongs, odds_val = _parse_cell_odds(cell)
                    if len(strongs) >= 2 and odds_val:
                        try:
                            data[f'tw_spread_{label}'] = float(strongs[1].strip())
                            data[f'tw_spread_{label}_odds'] = odds_val
                        except (ValueError, TypeError):
                            pass

            # 運彩大小（td-bank-bet02）
            for ou_label, cells in [('over', away_cells), ('under', home_cells)]:
                cell = cells.get('td-bank-bet02', '')
                if cell.strip():
                    strongs, odds_val = _parse_cell_odds(cell)
                    if len(strongs) >= 2 and odds_val:
                        try:
                            data['tw_total_line'] = float(strongs[1].strip())
                            data[f'tw_{ou_label}_odds'] = odds_val
                        except (ValueError, TypeError):
                            pass

            # 運彩不讓分（td-bank-bet03）— 獨立提取，避免跨 td
            for label, cells in [('away', away_cells), ('home', home_cells)]:
                cell = cells.get('td-bank-bet03', '')
                if cell.strip():
                    strongs, odds_val = _parse_cell_odds(cell)
                    # 不讓分格式：<strong>賠率</strong> 或 <strong>主/客</strong><strong>賠率</strong>
                    for s in strongs:
                        s = s.strip()
                        try:
                            val = float(s)
                            if val > 1.0:  # 賠率一定 > 1
                                data[f'tw_ml_{label}'] = val
                                break
                        except ValueError:
                            continue

            # 國際大小結果
            cell = away_cells.get('td-universal-bet02', '')
            if cell.strip():
                ou_m = re.search(r'<strong>([^<]*)</strong>', cell)
                pct_m = re.search(r'(\d+)%', cell)
                if ou_m:
                    data['intl_ou_result'] = ou_m.group(1).strip()
                if pct_m:
                    data['intl_ou_pct'] = pct_m.group(1)

            # 國際讓分結果
            cell = home_cells.get('td-universal-bet01', '')
            if cell.strip():
                sp_m = re.search(r'(\d+)分(贏|輸)', cell)
                if sp_m:
                    data['intl_spread_margin'] = int(sp_m.group(1))
                    data['intl_spread_result'] = sp_m.group(2)

    # ── 過盤率區塊 ──
    cover_block = re.search(r'過盤率([\s\S]*?)(?:運彩盤戰績統計|$)', html)
    if cover_block:
        cb = cover_block.group(1)

        # 對戰過盤率
        h2h_section = re.search(r'對戰([\s\S]*?)(?:本季|$)', cb)
        if h2h_section:
            h2h = h2h_section.group(1)
            # 讓分過盤率
            spread_pcts = re.findall(r'<strong>(\d+-\d+)</strong>\s*<span[^>]*>(\d+)%', h2h)
            if len(spread_pcts) >= 2:
                data['h2h_away_spread_record'] = spread_pcts[0][0]
                data['h2h_away_spread_pct'] = spread_pcts[0][1]

        # 本季過盤率
        season_section = re.search(r'本季([\s\S]*?)$', cb)
        if season_section:
            ss = season_section.group(1)
            season_pcts = re.findall(r'<strong>(\d+-\d+)</strong>\s*<span[^>]*>(\d+)%', ss)
            if season_pcts:
                data['season_cover_rates'] = [
                    {'record': p[0], 'pct': int(p[1])} for p in season_pcts
                ]

    return data if data else None


# ═══════════════════════════════════════════════════════
#  2. 賽事結果頁面（result）— 取得當日所有賽事結果與盤口
# ═══════════════════════════════════════════════════════

def fetch_results(sport='basketball', date_str=None):
    """
    從賽事結果查詢頁面取得指定日期的所有賽事結果。
    date_str: YYYY/MM/DD 格式，預設當天
    回傳 list of dict
    """
    alliance_id = SPORT_ALLIANCE.get(sport, '3')
    cache_key = f'result_{alliance_id}_{date_str or "today"}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    url = f'https://www.playsport.cc/gamesData/result?allianceid={alliance_id}'
    if date_str:
        url += f'&date={date_str}'

    html = _safe_get(url)
    if not html:
        return []

    results = _parse_result_html(html)
    _set_cached(cache_key, results)
    return results


def _parse_result_html(html):
    """解析賽事結果頁面"""
    games = []

    # 找所有比賽區塊（每場比賽由 gameid 連結識別）
    blocks = re.findall(
        r'gameid=(\d+)[\s\S]*?'
        r'(\d+)\s*</(?:li|strong|span)>[\s\S]*?V\.S\.[\s\S]*?'
        r'(\d+)\s*</(?:li|strong|span)>[\s\S]*?'
        r'teamid=(\d+)[^>]*>([^<]+)</a>[\s\S]*?'
        r'teamid=(\d+)[^>]*>([^<]+)</a>',
        html
    )

    for match in blocks:
        gameid, score1, score2, tid1, name1, tid2, name2 = match
        games.append({
            'gameid': gameid,
            'away_name': name1.strip(),
            'away_score': int(score1),
            'away_teamid': tid1,
            'home_name': name2.strip(),
            'home_score': int(score2),
            'home_teamid': tid2,
        })

    return games


# ═══════════════════════════════════════════════════════
#  3. 整合函數 — 取得比賽的完整賠率資料
# ═══════════════════════════════════════════════════════

def get_game_odds(game):
    """
    取得比賽的實際賠率，用於 EV 計算。
    回傳 dict:
    {
        'spread_val': float,      # 讓分值（主隊視角）
        'spread_odds': float,     # 讓分賠率
        'total_line': float,      # 大小分盤口線
        'over_odds': float,       # 大分賠率
        'under_odds': float,      # 小分賠率
        'ml_home': float,         # 主隊獨贏賠率
        'ml_away': float,         # 客隊獨贏賠率
        'source': str,            # 資料來源
    }
    """
    battle = fetch_battle_odds(game)
    if not battle:
        return None

    odds = {'source': 'playsport_battle'}

    # 讓分
    if 'tw_spread_home' in battle:
        odds['spread_val'] = battle['tw_spread_home']
        odds['spread_odds'] = battle.get('tw_spread_home_odds', 1.91)
    elif 'tw_spread_away' in battle:
        odds['spread_val'] = -battle['tw_spread_away']
        odds['spread_odds'] = battle.get('tw_spread_away_odds', 1.91)

    # 大小
    if 'tw_total_line' in battle:
        odds['total_line'] = battle['tw_total_line']
        odds['over_odds'] = battle.get('tw_over_odds', 1.91)
        odds['under_odds'] = battle.get('tw_under_odds', 1.91)

    # 不讓分
    if 'tw_ml_home' in battle:
        odds['ml_home'] = battle['tw_ml_home']
    if 'tw_ml_away' in battle:
        odds['ml_away'] = battle['tw_ml_away']

    # 過盤率資料
    if battle.get('season_cover_rates'):
        odds['season_cover_rates'] = battle['season_cover_rates']

    return odds if len(odds) > 1 else None


# ═══════════════════════════════════════════════════════
#  4. 足球 predict 頁面 — 取得不讓分賠率與大小分盤口
# ═══════════════════════════════════════════════════════

def fetch_soccer_predict(gameday='today', alliance_ids=None):
    """
    從 playsport.cc predict 頁面爬取足球盤口賠率。
    gameday: 'today' / 'tomorrow' / 'yesterday'
    回傳 dict: {(away_zh, home_zh): odds_dict}
    odds_dict keys: ml_away, ml_home, ml_draw, ou_line, over_odds, under_odds
    """
    if alliance_ids is None:
        alliance_ids = SOCCER_PREDICT_ALLIANCES

    cache_key = f'soccer_predict_{gameday}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    result = {}
    for aid in alliance_ids:
        url = f'https://www.playsport.cc/predict/games?allianceid={aid}&gameday={gameday}'
        html = _safe_get(url)
        if not html:
            continue
        games = _parse_soccer_predict_html(html)
        result.update(games)

    _set_cached(cache_key, result)
    return result


def _parse_soccer_predict_html(html):
    """
    解析 predict 頁面的足球盤口資料。
    以 gameid 為邊界提取整個比賽區塊（避免 rowspan inner table 截斷問題）。
    """
    games = {}

    game_ids = re.findall(r'<tr[^>]+gameid="(\d+)"', html)
    game_ids = list(dict.fromkeys(game_ids))

    for i, gid in enumerate(game_ids):
        # 以此 gameid 第一次出現到下一個 gameid（或頁尾）作為 block 邊界
        start = html.find(f'gameid="{gid}"')
        next_id = game_ids[i + 1] if i + 1 < len(game_ids) else None
        end = html.find(f'gameid="{next_id}"', start + 10) if next_id else len(html)
        block = html[start:end]

        # 隊伍名稱
        away_m = re.search(r'class="secondteam">([^<]+)<', block)
        home_m = re.search(r'class="(?:winnerteam|firstteam)"[^>]*>(?:<a[^>]*>)?([^<]+?)(?:</a>)?<', block)
        if not away_m or not home_m:
            continue
        away = away_m.group(1).strip()
        home = home_m.group(1).strip()
        if not away or not home:
            continue

        # 賠率提取 ── 用 <!--不讓分*客/主/和--> 和 <!--大小分*大/小--> 做定位錨點
        ml = {}
        ou = {}

        for side_label, dict_key in [('客', 'ml_away'), ('主', 'ml_home')]:
            anchor = f'<!--不讓分*{side_label}-->'
            idx = block.find(anchor)
            if idx < 0:
                continue
            cell = block[idx:idx + 400]
            # 賠率：<span>N.NN</span> 格式（無逗號）
            odds_m = re.search(r'<span>([\d.]+)</span>', cell)
            if odds_m:
                try:
                    ml[dict_key] = float(odds_m.group(1))
                except ValueError:
                    pass

        # 和盤在 td-bank-bet01 或 <!--不讓分*和--> 之後
        for anchor in ['<!--不讓分*和-->', 'td-bank-bet01']:
            idx = block.find(anchor)
            if idx >= 0:
                cell = block[idx:idx + 300]
                odds_m = re.search(r'<span>([\d.]+)</span>', cell)
                if odds_m:
                    try:
                        ml['ml_draw'] = float(odds_m.group(1))
                    except ValueError:
                        pass
                break

        for side_label, dict_key in [('大', 'over_odds'), ('小', 'under_odds')]:
            anchor = f'<!--大小分*{side_label}-->'
            idx = block.find(anchor)
            if idx < 0:
                continue
            cell = block[idx:idx + 400]
            # 大小分有 line：<strong>2.5</strong><span>, 2.15</span>（逗號可選）
            line_m = re.search(r'<strong[^>]*>([\d.]+)</strong>', cell)
            odds_m = (re.search(r'<span[^>]*>,\s*([\d.]+)\s*</span>', cell) or
                      re.search(r'<span[^>]*>\s*(1\.[3-9]\d|2\.[0-4]\d)\s*</span>', cell))
            if odds_m:
                try:
                    ou[dict_key] = float(odds_m.group(1))
                    if line_m and 'ou_line' not in ou:
                        ou['ou_line'] = float(line_m.group(1))
                except ValueError:
                    pass

        # 若 comment 方式未抓到 ou_line，嘗試 td-bank-bet02 class 備援
        if 'ou_line' not in ou:
            ou_td_m = re.search(
                r'td-bank-bet02[^>]*>[\s\S]*?<strong[^>]*>([\d.]+)</strong>[\s\S]*?<span[^>]*>,?\s*([\d.]+)',
                block
            )
            if ou_td_m:
                try:
                    ou['ou_line'] = float(ou_td_m.group(1))
                    ou.setdefault('over_odds', float(ou_td_m.group(2)))
                except ValueError:
                    pass

        entry = {**ml, **ou}
        if entry:
            games[(away, home)] = entry

    return games


def get_soccer_game_odds(away_zh, home_zh, gameday='today'):
    """
    取得足球場次的實際賠率（不讓分 + 大小分）。
    回傳 dict 或 None
    """
    data = fetch_soccer_predict(gameday)
    # 精確匹配
    if (away_zh, home_zh) in data:
        return data[(away_zh, home_zh)]
    # 模糊匹配（隊名截斷）
    for (a, h), odds in data.items():
        if (away_zh[:3] in a or a[:3] in away_zh) and (home_zh[:3] in h or h[:3] in home_zh):
            return odds
    return None


if __name__ == '__main__':
    # 測試
    test_game = {
        'gameId': '31001',
        'date': '2026-03-14',
        'home': '活塞',
        'away': '灰熊',
    }
    result = fetch_battle_odds(test_game)
    if result:
        print('Battle odds:')
        for k, v in result.items():
            print(f'  {k}: {v}')
    else:
        print('No battle odds found')

    odds = get_game_odds(test_game)
    if odds:
        print('\nGame odds:')
        for k, v in odds.items():
            print(f'  {k}: {v}')
