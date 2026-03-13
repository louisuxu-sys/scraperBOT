# -*- coding: utf-8 -*-
"""
球隊歷史賽事模組
從 playsport.cc /gamesData/teams 頁面爬取球隊近期比賽紀錄，
包含比分、讓分結果、大小分結果、賠率、預測比例等。
"""
import re
import time
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}

# ─── 快取 ───
_cache = {}
CACHE_TTL = 600  # 10 分鐘

# ─── 隊名 → teamid 對照表（playsport gamesData 用） ───
# NBA (allianceid=3)
NBA_TEAMS = {
    '塞爾提克': 31, '塞爾提': 31, '籃網': 32, '尼克': 33, '76人': 34, '暴龍': 35,
    '公牛': 36, '騎士': 37, '活塞': 38, '溜馬': 39, '公鹿': 40,
    '老鷹': 41, '黃蜂': 42, '熱火': 43, '魔術': 44, '巫師': 45,
    '金塊': 46, '灰狼': 47, '雷霆': 48, '拓荒者': 49, '爵士': 50,
    '勇士': 56, '快艇': 57, '湖人': 58, '太陽': 59, '國王': 60,
    '獨行俠': 51, '火箭': 52, '灰熊': 53, '鵜鶘': 54, '馬刺': 55,
}

# MLB (allianceid=1)
MLB_TEAMS = {
    '洋基': 1, '紅襪': 2, '藍鳥': 3, '金鶯': 4, '光芒': 5,
    '白襪': 6, '守護者': 7, '老虎': 8, '皇家': 9, '雙城': 10,
    '太空人': 11, '天使': 12, '運動家': 13, '水手': 14, '遊騎兵': 15,
    '勇士': 16, '馬林魚': 17, '大都會': 18, '費城人': 19, '國民': 20,
    '小熊': 21, '紅人': 22, '釀酒人': 23, '海盜': 24, '紅雀': 25,
    '響尾蛇': 26, '落磯': 27, '道奇': 28, '教士': 29, '巨人': 30,
}

# 聯賽 ID → 隊名表
LEAGUE_TEAM_MAP = {
    '3': NBA_TEAMS,
    '1': MLB_TEAMS,
}

# 運動類型 → 聯賽 ID
SPORT_LEAGUE_MAP = {
    'basketball': '3',
    'baseball': '1',
}


def _safe_get(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.encoding = 'utf-8'
        return resp.text
    except Exception as e:
        print(f'[TeamHistory] fetch error: {e}')
        return None


def _get_cached(key):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _set_cache(key, data):
    _cache[key] = (time.time(), data)


def get_team_id(team_name, sport='basketball'):
    """根據隊名取得 playsport teamid"""
    league_id = SPORT_LEAGUE_MAP.get(sport, '3')
    team_map = LEAGUE_TEAM_MAP.get(league_id, {})
    # 精確匹配
    if team_name in team_map:
        return team_map[team_name], league_id
    # 子字串匹配
    for name, tid in team_map.items():
        if name in team_name or team_name in name:
            return tid, league_id
    return None, league_id


def fetch_team_history(team_name, sport='basketball'):
    """
    爬取指定球隊的近期歷史賽事。
    回傳 dict:
    {
        'team': str,
        'games': [
            {
                'date': '03/10',
                'time': 'AM 09:00',
                'opponent': '公鹿',
                'team_score': 117,
                'opp_score': 128,
                'is_home': True,
                'win': False,
                'intl_spread_result': '贏' or '輸' or None,
                'intl_spread_margin': 7,
                'intl_ou_result': '大' or '小' or None,
                'intl_ou_line': 229,
                'tw_spread': 6.5,
                'tw_spread_odds': '1.75',
                'tw_ml_odds': '1.32',
                'tw_ou_line': 228.5,
                'tw_ou_odds_over': '1.73',
                'tw_ou_odds_under': '1.70',
                'predict_spread_pct': '62%',
                'predict_ml_pct': '84%',
                'predict_ou_pct': '59%',
            },
            ...
        ],
        'summary': {
            'total': int,
            'wins': int,
            'losses': int,
            'ats_wins': int,    # 讓分過盤次數
            'ats_losses': int,
            'over_count': int,  # 大分次數
            'under_count': int,
            'avg_scored': float,
            'avg_allowed': float,
        }
    }
    """
    team_id, league_id = get_team_id(team_name, sport)
    if not team_id:
        print(f'[TeamHistory] Unknown team: {team_name}')
        return None

    cache_key = f'history_{league_id}_{team_id}'
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    url = f'https://www.playsport.cc/gamesData/teams?allianceid={league_id}&teamid={team_id}'
    html = _safe_get(url)
    if not html:
        return None

    games = _parse_history_html(html, team_name)
    if not games:
        print(f'[TeamHistory] No games parsed for {team_name} (tid={team_id})')
        return None

    # 計算摘要
    summary = _calc_summary(games)

    result = {
        'team': team_name,
        'team_id': team_id,
        'games': games,
        'summary': summary,
    }

    _set_cache(cache_key, result)
    print(f'[TeamHistory] {team_name}: {len(games)} games, '
          f'{summary["wins"]}W-{summary["losses"]}L, '
          f'ATS {summary["ats_wins"]}-{summary["ats_losses"]}, '
          f'O/U {summary["over_count"]}-{summary["under_count"]}')
    return result


def _parse_history_html(html, team_name):
    """解析 gamesData/teams 頁面的歷史賽事 HTML"""
    games = []

    # 找到歷史賽事區塊（在 #tab1 / historyGame 區塊內）
    # 每場比賽由兩個 <tr> 組成，中間以 gaprow 分隔
    # 用 gaprow 分割比賽區塊
    blocks = re.split(r'<tr\s+class="gaprow">', html)

    for block in blocks:
        game = _parse_game_block(block, team_name)
        if game:
            games.append(game)

    return games


def _strip(s):
    return re.sub(r'<[^>]+>', '', s).strip()


def _parse_game_block(block, team_name):
    """解析單場比賽的兩個 <tr> 區塊"""
    # 日期時間
    date_m = re.search(r'td-gameinfo[\s\S]*?<h3>(.*?)</h3>', block)
    time_m = re.search(r'<h4>(.*?)</h4>', block)
    if not date_m:
        return None

    game_date = _strip(date_m.group(1))
    game_time = _strip(time_m.group(1)) if time_m else ''

    # 比分和隊名
    scores = re.findall(r'<li\s*(?:class="[^"]*")?>\s*(\d+|V\.S\.)\s*</li>', block)
    if len(scores) < 3:
        return None

    score1 = scores[0] if scores[0] != 'V.S.' else None
    score2 = scores[2] if scores[2] != 'V.S.' else None
    if not score1 or not score2:
        return None

    score1 = int(score1)
    score2 = int(score2)

    # 上方隊名（第一行）和下方隊名（第二行）
    winner_m = re.search(r'class="winnerteam">\s*(.*?)\s*(?:</td>|<)', block)
    second_m = re.search(r'class="secondteam">\s*(.*?)\s*(?:</td>|<)', block)
    if not winner_m or not second_m:
        return None

    winner_name = _strip(winner_m.group(1))
    second_name = _strip(second_m.group(1))

    # 判斷上方/下方哪個是查詢的隊伍
    # winnerscores class 在哪個 <li> 上決定比分順序
    winner_first = bool(re.search(r'<li\s+class="winnerscores"', block.split('vsicon')[0]))

    if winner_first:
        # winner 在上方（score1），second 在下方（score2）
        top_team = winner_name
        top_score = score1
        bot_team = second_name
        bot_score = score2
    else:
        # second 在上方（score1），winner 在下方（score2）
        top_team = second_name
        top_score = score1
        bot_team = winner_name
        bot_score = score2

    # 找出查詢的隊伍是哪個
    if team_name in top_team or top_team in team_name:
        my_team = top_team
        my_score = top_score
        opp_team = bot_team
        opp_score = bot_score
        is_home = False  # 上方通常是客隊
    elif team_name in bot_team or bot_team in team_name:
        my_team = bot_team
        my_score = bot_score
        opp_team = top_team
        opp_score = top_score
        is_home = True  # 下方通常是主隊
    else:
        return None

    win = my_score > opp_score

    # 解析第一個 tr 的盤口資料（上方隊伍）
    rows = list(re.finditer(r'<tr[^>]*>([\s\S]*?)</tr>', block))
    if len(rows) < 2:
        return None

    row1 = rows[0].group(1)  # 上方隊伍的 betting data
    row2 = rows[1].group(1) if len(rows) > 1 else ''  # 下方隊伍

    # 取查詢隊伍的那一行
    my_row = row2 if is_home else row1

    # 國際讓分結果
    intl_spread_result = None
    intl_spread_margin = None
    iah_m = re.search(r'iAhead[\s\S]*?<strong>(.*?)</strong>[\s\S]*?<strong>\s*(\d+)\s*(贏|輸)', my_row)
    if iah_m:
        intl_spread_margin = int(iah_m.group(2))
        intl_spread_result = iah_m.group(3)

    # 國際大小結果
    intl_ou_result = None
    intl_ou_line = None
    iou_m = re.search(r'iOu[\s\S]*?<strong[^>]*>(大|小)</strong>', my_row)
    if iou_m:
        intl_ou_result = iou_m.group(1)
    iou_line_m = re.search(r'iOu[\s\S]*?<strong>\s*(\d+)', my_row)
    if iou_line_m:
        intl_ou_line = int(iou_line_m.group(1))

    # 運彩讓分
    tw_spread = None
    tw_spread_odds = None
    ahead_m = re.search(r'class="ahead[\s\S]*?([+-]?)\s*([\d.]+)\s*</span>\s*<span>,([\d.]+)</span>', my_row)
    if ahead_m:
        sign = ahead_m.group(1)
        tw_spread = float(ahead_m.group(2))
        if sign == '+':
            tw_spread = tw_spread  # 受讓
        else:
            tw_spread = -tw_spread  # 讓分
        tw_spread_odds = ahead_m.group(3)

    # 運彩獨贏賠率
    tw_ml_odds = None
    ml_m = re.search(r'class="su[\s\S]*?<span>,([\d.]+)</span>', my_row)
    if ml_m:
        tw_ml_odds = ml_m.group(1)

    # 運彩大小
    tw_ou_line = None
    tw_ou_odds = None
    ou_m = re.search(r'class="ou"[\s\S]*?<strong[^>]*>([\d.]+)</strong>\s*<span>,([\d.]+)</span>', my_row)
    if ou_m:
        tw_ou_line = float(ou_m.group(1))
        tw_ou_odds = ou_m.group(2)

    # 預測比例（取 predict-s 欄位）
    pct_matches = re.findall(r'predict-s[^"]*">\s*([\d]+)%', my_row)

    game = {
        'date': game_date,
        'time': game_time,
        'opponent': opp_team,
        'team_score': my_score,
        'opp_score': opp_score,
        'is_home': is_home,
        'win': win,
        'intl_spread_result': intl_spread_result,
        'intl_spread_margin': intl_spread_margin,
        'intl_ou_result': intl_ou_result,
        'intl_ou_line': intl_ou_line,
        'tw_spread': tw_spread,
        'tw_spread_odds': tw_spread_odds,
        'tw_ml_odds': tw_ml_odds,
        'tw_ou_line': tw_ou_line,
        'tw_ou_odds': tw_ou_odds,
        'predict_pcts': pct_matches,
    }
    return game


def _calc_summary(games):
    """計算歷史賽事摘要統計"""
    total = len(games)
    wins = sum(1 for g in games if g['win'])
    losses = total - wins
    ats_wins = sum(1 for g in games if g.get('intl_spread_result') == '贏')
    ats_losses = sum(1 for g in games if g.get('intl_spread_result') == '輸')
    over_count = sum(1 for g in games if g.get('intl_ou_result') == '大')
    under_count = sum(1 for g in games if g.get('intl_ou_result') == '小')
    avg_scored = sum(g['team_score'] for g in games) / total if total else 0
    avg_allowed = sum(g['opp_score'] for g in games) / total if total else 0

    # 近 5 場
    recent = games[:5]
    recent_wins = sum(1 for g in recent if g['win'])
    recent_ats = sum(1 for g in recent if g.get('intl_spread_result') == '贏')
    recent_over = sum(1 for g in recent if g.get('intl_ou_result') == '大')

    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'win_pct': round(wins / total * 100, 1) if total else 0,
        'ats_wins': ats_wins,
        'ats_losses': ats_losses,
        'ats_pct': round(ats_wins / (ats_wins + ats_losses) * 100, 1) if (ats_wins + ats_losses) else 0,
        'over_count': over_count,
        'under_count': under_count,
        'over_pct': round(over_count / (over_count + under_count) * 100, 1) if (over_count + under_count) else 0,
        'avg_scored': round(avg_scored, 1),
        'avg_allowed': round(avg_allowed, 1),
        'recent_5_wins': recent_wins,
        'recent_5_ats': recent_ats,
        'recent_5_over': recent_over,
    }


def get_matchup_history(home_name, away_name, sport='basketball'):
    """
    取得兩隊的歷史資料，回傳用於分析的摘要。
    """
    home_hist = fetch_team_history(home_name, sport)
    away_hist = fetch_team_history(away_name, sport)
    return home_hist, away_hist


if __name__ == '__main__':
    # 測試
    result = fetch_team_history('熱火', 'basketball')
    if result:
        print(f"\n{result['team']} 近期 {result['summary']['total']} 場:")
        print(f"  勝敗: {result['summary']['wins']}W-{result['summary']['losses']}L "
              f"({result['summary']['win_pct']}%)")
        print(f"  ATS: {result['summary']['ats_wins']}-{result['summary']['ats_losses']} "
              f"({result['summary']['ats_pct']}%)")
        print(f"  O/U: {result['summary']['over_count']}-{result['summary']['under_count']} "
              f"({result['summary']['over_pct']}% over)")
        print(f"  場均: {result['summary']['avg_scored']} 得 / {result['summary']['avg_allowed']} 失")
        print(f"\n最近 5 場:")
        for g in result['games'][:5]:
            loc = '主' if g['is_home'] else '客'
            w = '✅' if g['win'] else '❌'
            print(f"  {g['date']} {loc} vs {g['opponent']} "
                  f"{g['team_score']}:{g['opp_score']} {w}")
