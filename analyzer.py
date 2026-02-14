"""
AI 分析引擎（規則式）
移植自 sports-analysis.html 的 generateAnalysis 函數
"""
import re


def parse_record(s):
    """解析戰績字串：'30勝25敗' / '33 - 19' / '客12 - 13' / '8 - 2 , 5連勝'"""
    if not s:
        return None
    # 格式1: X勝Y敗
    m = re.search(r'(\d+)\s*勝\s*(\d+)\s*敗', s)
    if m:
        w, l = int(m.group(1)), int(m.group(2))
        total = w + l
        pct = round(w / total * 100, 1) if total > 0 else 0
        return {'w': w, 'l': l, 'total': total, 'pct': pct}
    # 格式2: X - Y
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)', s)
    if m:
        w, l = int(m.group(1)), int(m.group(2))
        total = w + l
        pct = round(w / total * 100, 1) if total > 0 else 0
        return {'w': w, 'l': l, 'total': total, 'pct': pct}
    return None


def parse_avg_score(s):
    """解析 '113.5 / 108.2' 格式（得分/失分）"""
    if not s:
        return None
    m = re.search(r'([\d.]+)\s*/\s*([\d.]+)', s)
    if m:
        return {'scored': float(m.group(1)), 'allowed': float(m.group(2))}
    return None


def generate_analysis(game, sport='basketball'):
    """
    生成 AI 賽前分析
    game: 從 scraper 取得的 game dict
    sport: 運動類型
    回傳: dict { homeWin, draw, awayWin, suggestion, confidence }
    """
    is_bball = sport == 'basketball'
    home_win = 45
    draw = 0 if is_bball else 25
    away_win = 55 if is_bball else 30
    confidence = 50

    rec = game.get('record', {})
    odds = game.get('odds', {})
    spread_str = odds.get('spread', '')
    try:
        spread = float(spread_str)
        has_spread = spread != 0
    except (ValueError, TypeError):
        spread = 0
        has_spread = False

    home_name = game.get('home', '主隊')
    away_name = game.get('away', '客隊')

    # 解析數據
    home_rec = parse_record(rec.get('homeRecord'))
    away_rec = parse_record(rec.get('awayRecord'))
    home_recent = parse_record(rec.get('homeRecent'))
    away_recent = parse_record(rec.get('awayRecent'))
    home_avg = parse_avg_score(rec.get('homeAvg'))
    away_avg = parse_avg_score(rec.get('awayAvg'))
    home_ha = parse_record(rec.get('homeHomeAway'))
    away_ha = parse_record(rec.get('awayHomeAway'))

    # ===== 已結束 =====
    if game.get('status') == 'finished' and game.get('homeScore') is not None:
        hs, a_s = int(game['homeScore']), int(game['awayScore'])
        diff = hs - a_s
        winner = home_name if diff > 0 else away_name
        loser = away_name if diff > 0 else home_name
        margin = abs(diff)

        lines = []
        if margin >= 15:
            desc = '大幅領先取得壓倒性勝利'
        elif margin >= 8:
            desc = '穩定發揮拉開差距'
        else:
            desc = '雙方纏鬥至終場'
        lines.append(f'本場比賽由 {winner} 以 {max(hs, a_s)}:{min(hs, a_s)} 擊敗 {loser}，{desc}。')

        if has_spread:
            fav = home_name if spread > 0 else away_name
            abs_spread = abs(spread)
            covered = (diff > spread) if spread > 0 else (diff < spread)
            lines.append(f'盤口方面，{fav} 讓 {abs_spread} 分，{"成功過盤" if covered else "未能過盤"}。')

        home_win = 70 if diff > 0 else 25
        away_win = (100 - home_win) if is_bball else (60 if diff < 0 else 20)
        draw = 0 if is_bball else (100 - home_win - away_win)
        confidence = 90
        total = home_win + draw + away_win
        home_win = round(home_win / total * 100)
        away_win = round(away_win / total * 100)
        draw = 100 - home_win - away_win
        return {
            'homeWin': home_win, 'draw': draw, 'awayWin': away_win,
            'suggestion': '\n'.join(lines), 'confidence': confidence
        }

    # ===== 進行中 =====
    if game.get('status') == 'live':
        hs = int(game.get('homeScore', 0) or 0)
        a_s = int(game.get('awayScore', 0) or 0)
        diff = hs - a_s
        lines = []
        if diff > 0:
            lines.append(f'比賽進行中，{home_name} 以 {hs}:{a_s} 領先 {abs(diff)} 分，掌握場上主動權。')
        elif diff < 0:
            lines.append(f'比賽進行中，{away_name} 以 {a_s}:{hs} 領先 {abs(diff)} 分，客場表現強勢。')
        else:
            lines.append(f'比賽進行中，雙方 {hs}:{a_s} 戰成平手，比賽膠著。')

        home_win = 62 if diff > 0 else (35 if diff < 0 else 48)
        away_win = (100 - home_win) if is_bball else (55 if diff < 0 else 30)
        draw = 0 if is_bball else (100 - home_win - away_win)
        confidence = 55
        total = home_win + draw + away_win
        home_win = round(home_win / total * 100)
        away_win = round(away_win / total * 100)
        draw = 100 - home_win - away_win
        return {
            'homeWin': home_win, 'draw': draw, 'awayWin': away_win,
            'suggestion': '\n'.join(lines), 'confidence': confidence
        }

    # ===== 賽前分析（核心） =====
    lines = []
    home_adj = 0
    away_adj = 0

    # 1. 整體戰績
    if home_rec and away_rec:
        h_pct, a_pct = home_rec['pct'], away_rec['pct']
        lines.append(f'【整體戰績】{home_name}（{home_rec["w"]}勝{home_rec["l"]}敗，勝率 {h_pct}%）vs {away_name}（{away_rec["w"]}勝{away_rec["l"]}敗，勝率 {a_pct}%）。')
        if h_pct - a_pct > 15:
            lines.append(f'{home_name} 整體戰績明顯優於對手，具備較強的陣容深度與穩定性。')
            home_adj += 8
        elif a_pct - h_pct > 15:
            lines.append(f'{away_name} 本季表現更為出色，整體實力佔優。')
            away_adj += 8
        else:
            lines.append('兩隊本季戰績相近，實力在伯仲之間。')

    # 2. 近況
    if home_recent and away_recent:
        h_r, a_r = home_recent['w'], away_recent['w']
        lines.append(f'【近期狀態】{home_name} 近十場 {home_recent["w"]}勝{home_recent["l"]}敗；{away_name} 近十場 {away_recent["w"]}勝{away_recent["l"]}敗。')
        if h_r >= 7:
            lines.append(f'{home_name} 近期手感火燙，處於連勝節奏中。')
            home_adj += 5
        elif h_r <= 3:
            lines.append(f'{home_name} 近況低迷，需留意狀態調整。')
            away_adj += 3
        if a_r >= 7:
            lines.append(f'{away_name} 近期狀態極佳，客場作戰信心充足。')
            away_adj += 5
        elif a_r <= 3:
            lines.append(f'{away_name} 近期表現不穩，客場挑戰難度加大。')
            home_adj += 3

    # 3. 主客場戰績
    if home_ha and away_ha:
        lines.append(f'【主客場】{home_name} 主場 {home_ha["w"]}勝{home_ha["l"]}敗；{away_name} 客場 {away_ha["w"]}勝{away_ha["l"]}敗。')
        h_ha_pct = home_ha['pct']
        a_ha_pct = away_ha['pct']
        if h_ha_pct > 60:
            lines.append(f'{home_name} 主場勝率突出，主場龍優勢不容忽視。')
            home_adj += 4
        if a_ha_pct < 40:
            lines.append(f'{away_name} 客場戰績不佳，客場蟲劣勢明顯。')
            home_adj += 3
        elif a_ha_pct > 55:
            lines.append(f'{away_name} 客場表現穩健，具備客場搶分能力。')
            away_adj += 3

    # 4. 得失分
    if home_avg and away_avg:
        lines.append(f'【攻防數據】{home_name} 場均得 {home_avg["scored"]} 失 {home_avg["allowed"]} 分；{away_name} 場均得 {away_avg["scored"]} 失 {away_avg["allowed"]} 分。')
        h_net = home_avg['scored'] - home_avg['allowed']
        a_net = away_avg['scored'] - away_avg['allowed']
        if h_net > 5 and a_net < -3:
            lines.append(f'{home_name} 攻守兩端均佔優勢，淨勝分差距顯著。')
        elif a_net > 5 and h_net < -3:
            lines.append(f'{away_name} 攻防效率更高，數據面具有明顯優勢。')
        elif home_avg['scored'] > away_avg['scored'] + 5:
            lines.append(f'{home_name} 進攻火力更強，場均得分領先對手。')
        elif away_avg['scored'] > home_avg['scored'] + 5:
            lines.append(f'{away_name} 進攻端更具威脅，得分能力佔優。')

        if is_bball:
            expected_total = (home_avg['scored'] + away_avg['scored'] + home_avg['allowed'] + away_avg['allowed']) / 2
            if expected_total > 225:
                lines.append(f'預計本場節奏偏快，大分機率較高（預估總分 {expected_total:.0f} 分上下）。')
            elif expected_total < 210:
                lines.append(f'雙方防守強度較高，小分值得關注（預估總分 {expected_total:.0f} 分上下）。')

    # 5. 盤口
    if has_spread:
        fav = home_name if spread > 0 else away_name
        dog = away_name if spread > 0 else home_name
        abs_spread = abs(spread)
        line_text = f'【盤口解讀】本場開出 {fav} 讓 {abs_spread} 分，'
        if abs_spread >= 10:
            line_text += f'讓分幅度較大，盤口看好 {fav} 大勝。建議留意 {dog} 是否具備爆冷實力。'
        elif abs_spread >= 5:
            line_text += f'屬於中等讓分，{fav} 被看好但需穩定發揮方能過盤。'
        else:
            line_text += '讓分較小，反映兩隊實力差距不大，比賽懸念較高。'
        lines.append(line_text)

        if spread > 0:
            home_adj += min(10, abs_spread)
        else:
            away_adj += min(10, abs_spread)

    # 6. 對戰紀錄
    h2h_home = parse_record(rec.get('homeH2H'))
    h2h_away = parse_record(rec.get('awayH2H'))
    if h2h_home and h2h_away:
        lines.append(f'【歷史交鋒】{home_name} {h2h_home["w"]}勝{h2h_home["l"]}敗 vs {away_name} {h2h_away["w"]}勝{h2h_away["l"]}敗。')
        if h2h_home['w'] > h2h_away['w'] + 2:
            lines.append(f'{home_name} 在歷史對戰中佔據心理優勢。')
            home_adj += 3
        elif h2h_away['w'] > h2h_home['w'] + 2:
            lines.append(f'{away_name} 在交手紀錄中更勝一籌。')
            away_adj += 3

    # 沒有任何數據
    if not lines:
        lines.append(f'本場比賽 {home_name}（主）迎戰 {away_name}（客），主隊擁有主場優勢。')
        lines.append('建議關注兩隊近期傷病動態與輪休情況，作為投注參考依據。')

    # 7. 總結
    total_adj = home_adj - away_adj
    if total_adj > 10:
        lines.append(f'📌 綜合評估：{home_name} 各項數據全面佔優，本場值得看好主勝方向。')
    elif total_adj > 4:
        lines.append(f'📌 綜合評估：{home_name} 略佔優勢，可適度關注主勝，但需注意客隊反撲能力。')
    elif total_adj < -10:
        lines.append(f'📌 綜合評估：{away_name} 綜合實力更強，客勝方向值得重點關注。')
    elif total_adj < -4:
        lines.append(f'📌 綜合評估：{away_name} 稍佔上風，客場搶分機會較大。')
    else:
        lines.append('📌 綜合評估：兩隊勢均力敵，比賽充滿變數，建議謹慎操作或觀望。')

    # 計算勝率
    home_win = 45 + home_adj - away_adj / 2
    away_win = 45 + away_adj - home_adj / 2
    home_win = max(15, min(80, home_win))
    away_win = max(15, min(80, away_win))

    if is_bball:
        draw = 0
        t2 = home_win + away_win
        home_win = round(home_win / t2 * 100)
        away_win = 100 - home_win
    else:
        draw = max(5, 100 - home_win - away_win)
        t2 = home_win + draw + away_win
        home_win = round(home_win / t2 * 100)
        away_win = round(away_win / t2 * 100)
        draw = 100 - home_win - away_win

    confidence = min(85, max(40, 50 + abs(total_adj) * 2))

    return {
        'homeWin': home_win,
        'draw': draw,
        'awayWin': away_win,
        'suggestion': '\n'.join(lines),
        'confidence': confidence,
    }


def format_game_text(game, sport='basketball'):
    """
    將一場比賽格式化為 LINE 訊息文字
    """
    status_map = {
        'live': '🔴 進行中',
        'upcoming': '⏳ 未開始',
        'finished': '✅ 已結束',
        'postponed': '⚠️ 延期',
    }
    status = status_map.get(game.get('status'), '未知')
    home = game.get('home', '—')
    away = game.get('away', '—')
    time_str = game.get('time', '')

    # 比分
    if game.get('homeScore') is not None:
        score = f"{game['homeScore']} : {game['awayScore']}"
    else:
        score = 'VS'

    # 盤口
    spread_text = ''
    spread_fav = ''
    odds = game.get('odds', {})
    spread_str = odds.get('spread', '')
    try:
        spread = float(spread_str)
        if spread != 0:
            spread_fav = home if spread > 0 else away
            spread_text = f'📌 推薦：{spread_fav} 讓{abs(spread)}'
    except (ValueError, TypeError):
        pass

    # 推薦獲勝標記
    win_mark = ''
    if game.get('status') == 'finished' and game.get('homeScore') is not None and spread_fav:
        hs = int(game['homeScore'])
        a_s = int(game['awayScore'])
        winner = home if hs > a_s else away
        if winner == spread_fav:
            win_mark = ' 🎯✔'

    # 快速推薦（讓分/受讓/獨贏/大小分）
    analysis = generate_analysis(game, sport)
    hw = analysis['homeWin']
    aw = analysis['awayWin']
    diff = abs(hw - aw)
    fav = home if hw >= aw else away
    dog = away if hw >= aw else home

    try:
        spread_val = float(odds.get('spread', '0'))
    except (ValueError, TypeError):
        spread_val = 0

    if diff > 20:
        recommend = f'🔮 推薦：{fav} 讓分'
    elif diff > 10:
        recommend = f'🔮 推薦：{fav} 獨贏'
    elif spread_val != 0:
        dog_team = away if spread_val > 0 else home
        recommend = f'🔮 推薦：{dog_team} 受讓'
    else:
        if analysis.get('confidence', 50) >= 55:
            recommend = f'🔮 推薦：推大分'
        else:
            recommend = f'🔮 推薦：推小分'

    lines = [
        f'━━━━━━━━━━━━━━━',
        f'{status}  {time_str}{win_mark}',
        f'🏠 {home}',
        f'🚌 {away}',
        f'📊 {score}',
        recommend,
    ]

    if spread_text:
        lines.append(spread_text)

    return '\n'.join(lines)


def format_analysis_text(game, sport='basketball'):
    """
    生成完整的 AI 分析訊息（用於 LINE Bot）
    """
    analysis = generate_analysis(game, sport)
    home = game.get('home', '—')
    away = game.get('away', '—')

    # 勝率長條圖
    hw = analysis['homeWin']
    aw = analysis['awayWin']
    bar_len = 10
    h_bar = '█' * round(hw / 100 * bar_len)
    a_bar = '█' * round(aw / 100 * bar_len)

    lines = [
        f'⚡ 賽事分析',
        f'━━━━━━━━━━━━━━━',
        f'🏠 {home}',
        f'🚌 {away}',
        f'',
        f'📈 勝率預測',
        f'主 {h_bar} {hw}%',
    ]

    if sport != 'basketball':
        dw = analysis['draw']
        d_bar = '█' * round(dw / 100 * bar_len)
        lines.append(f'平 {d_bar} {dw}%')

    lines.extend([
        f'客 {a_bar} {aw}%',
        f'',
        f'🎯 信心指數：{analysis["confidence"]}%',
    ])

    # 分析文字
    suggestion = analysis.get('suggestion', '')
    if suggestion:
        lines.append(f'')
        lines.append(f'━━━━━━━━━━━━━━━')
        lines.append(f'📝 分析建議')
        for line in suggestion.split('\n'):
            if line.strip():
                lines.append(f'{line.strip()}')

    return '\n'.join(lines)


def format_all_games_text(games, sport='basketball', date_str=''):
    """
    格式化所有比賽為 LINE 訊息
    """
    if not games:
        return (
            f'📅 {date_str}\n'
            f'━━━━━━━━━━━━━━━\n'
            f'目前沒有賽事資料，請稍後再試。'
        )

    sport_emoji = {
        'basketball': '🏀', 'baseball': '⚾',
        'soccer': '⚽', 'hockey': '🏒', 'tennis': '🎾'
    }.get(sport, '🏆')

    # 按聯賽分組
    groups = {}
    for g in games:
        league = g.get('league', '未知')
        if league not in groups:
            groups[league] = []
        groups[league].append(g)

    lines = [
        f'{sport_emoji} SPORTIQ 賽事',
        f'━━━━━━━━━━━━━━━',
    ]
    if date_str:
        lines.append(f'📅 {date_str}')
    lines.append(f'📊 共 {len(games)} 場賽事')
    lines.append('')

    for league, league_games in groups.items():
        lines.append(f'🏷 {league}【{len(league_games)} 場】')
        for g in league_games:
            lines.append(format_game_text(g, sport))
        lines.append('')

    if len(games) > 11:
        lines.append(f'👇 點擊按鈕或輸入「分析 隊名」查看詳細分析')
    else:
        lines.append(f'👇 點擊下方按鈕查看詳細分析')
    return '\n'.join(lines)
