"""
AI 分析引擎（規則式）
移植自 sports-analysis.html 的 generateAnalysis 函數
"""
import re
import math

try:
    from team_history import fetch_team_history, get_matchup_history
except ImportError:
    fetch_team_history = None
    get_matchup_history = None

try:
    from playsport_data import get_game_odds, get_soccer_game_odds
except ImportError:
    get_game_odds = None
    get_soccer_game_odds = None

try:
    from player_data import get_nba_team_injuries, get_mlb_game_pitchers, calc_injury_impact, calc_pitcher_quality
except ImportError:
    get_nba_team_injuries = None
    get_mlb_game_pitchers = None
    calc_injury_impact = None
    calc_pitcher_quality = None

try:
    from yahoo_data import get_mlb_game_info, calc_weather_impact, format_weather_text, format_pitcher_result
except ImportError:
    get_mlb_game_info = None
    calc_weather_impact = None
    format_weather_text = None
    format_pitcher_result = None


def _poisson_prob(k, lam):
    """泊松概率 P(X=k | lambda=lam)"""
    if k < 0 or lam <= 0:
        return 0.0
    try:
        return (lam ** k * math.exp(-lam)) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


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
    is_neutral = game.get('neutral', False)
    expected_total = 0

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

        # Yahoo 賽後投手結果（MLB）
        if sport == 'baseball' and game.get('leagueId') == '1' and get_mlb_game_info:
            try:
                yahoo_info = get_mlb_game_info(away_name, home_name)
                if yahoo_info and yahoo_info.get('pitchers'):
                    ptxt = format_pitcher_result(yahoo_info['pitchers'])
                    if ptxt:
                        lines.append(ptxt)
            except Exception:
                pass

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
        if is_neutral:
            lines.append(f'【戰績參考】{home_name} {home_ha["w"]}勝{home_ha["l"]}敗；{away_name} {away_ha["w"]}勝{away_ha["l"]}敗。（中立場地，無主客場優勢）')
        else:
            lines.append(f'【主客場】{home_name} 主場 {home_ha["w"]}勝{home_ha["l"]}敗；{away_name} 客場 {away_ha["w"]}勝{away_ha["l"]}敗。')
        h_ha_pct = home_ha['pct']
        a_ha_pct = away_ha['pct']
        if not is_neutral:
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
        else:
            expected_total = (home_avg['scored'] + away_avg['scored'] + home_avg['allowed'] + away_avg['allowed']) / 2

    # 5. 盤口（livescore 資料）
    if has_spread:
        abs_spread = abs(spread)
        # livescore 的 spread 無正負號，用已計算的 home_adj/away_adj 推斷讓分方
        if home_adj >= away_adj:
            ls_fav = home_name
            ls_dog = away_name
        else:
            ls_fav = away_name
            ls_dog = home_name
        line_text = f'【盤口解讀】本場開出讓 {abs_spread} 分，'
        if abs_spread >= 10:
            line_text += f'讓分幅度較大，比賽預計差距明顯。'
        elif abs_spread >= 5:
            line_text += f'屬於中等讓分，需穩定發揮方能過盤。'
        else:
            line_text += '讓分較小，反映兩隊實力差距不大，比賽懸念較高。'
        lines.append(line_text)

        if home_adj >= away_adj:
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

    # 7. 連勝/連敗趨勢偵測
    def detect_streak(record_str):
        """從戰績字串偵測連勝/連敗"""
        if not record_str:
            return 0
        m = re.search(r'(\d+)\s*連勝', record_str)
        if m:
            return int(m.group(1))
        m = re.search(r'(\d+)\s*連敗', record_str)
        if m:
            return -int(m.group(1))
        return 0

    h_streak = detect_streak(rec.get('homeRecent'))
    a_streak = detect_streak(rec.get('awayRecent'))
    if h_streak >= 3:
        lines.append(f'🔥 {home_name} 目前 {h_streak} 連勝，士氣高昂！')
        home_adj += min(5, h_streak)
    elif h_streak <= -3:
        lines.append(f'❄️ {home_name} 目前 {abs(h_streak)} 連敗，狀態堪憂。')
        away_adj += min(5, abs(h_streak))
    if a_streak >= 3:
        lines.append(f'🔥 {away_name} 目前 {a_streak} 連勝，客場氣勢強勁！')
        away_adj += min(5, a_streak)
    elif a_streak <= -3:
        lines.append(f'❄️ {away_name} 目前 {abs(a_streak)} 連敗，信心不足。')
        home_adj += min(5, abs(a_streak))

    # 8. 歷史賽事深度分析（playsport gamesData/teams）
    if fetch_team_history and game.get('status') != 'finished':
        try:
            if get_matchup_history:
                h_hist, a_hist = get_matchup_history(home_name, away_name, sport)
            else:
                h_hist = fetch_team_history(home_name, sport)
                a_hist = fetch_team_history(away_name, sport)

            if h_hist and h_hist.get('summary'):
                hs = h_hist['summary']
                lines.append(f'【{home_name} 近況】{hs["total"]}場 {hs["wins"]}勝{hs["losses"]}敗'
                             f'（勝率{hs["win_pct"]}%）'
                             f' | 場均得{hs["avg_scored"]} 失{hs["avg_allowed"]}')
                if hs['ats_wins'] + hs['ats_losses'] > 0:
                    lines.append(f'  讓分過盤 {hs["ats_wins"]}-{hs["ats_losses"]}'
                                 f'（{hs["ats_pct"]}%）'
                                 f' | 大分 {hs["over_count"]}-{hs["under_count"]}'
                                 f'（{hs["over_pct"]}%）')
                # 勝率高 → 加分
                if hs['win_pct'] >= 60:
                    home_adj += 3
                elif hs['win_pct'] <= 40:
                    away_adj += 2
                # ATS 趨勢加分
                if hs['ats_pct'] >= 60:
                    home_adj += 2
                elif hs['ats_pct'] <= 35:
                    away_adj += 1

            if a_hist and a_hist.get('summary'):
                as_ = a_hist['summary']
                lines.append(f'【{away_name} 近況】{as_["total"]}場 {as_["wins"]}勝{as_["losses"]}敗'
                             f'（勝率{as_["win_pct"]}%）'
                             f' | 場均得{as_["avg_scored"]} 失{as_["avg_allowed"]}')
                if as_['ats_wins'] + as_['ats_losses'] > 0:
                    lines.append(f'  讓分過盤 {as_["ats_wins"]}-{as_["ats_losses"]}'
                                 f'（{as_["ats_pct"]}%）'
                                 f' | 大分 {as_["over_count"]}-{as_["under_count"]}'
                                 f'（{as_["over_pct"]}%）')
                if as_['win_pct'] >= 60:
                    away_adj += 3
                elif as_['win_pct'] <= 40:
                    home_adj += 2
                if as_['ats_pct'] >= 60:
                    away_adj += 2
                elif as_['ats_pct'] <= 35:
                    home_adj += 1

            # 大小分趨勢對比
            if h_hist and a_hist:
                h_over = h_hist['summary'].get('over_pct', 50)
                a_over = a_hist['summary'].get('over_pct', 50)
                if h_over >= 60 and a_over >= 60:
                    lines.append('📊 兩隊近期大分趨勢明顯，本場大分值得關注。')
                elif h_over <= 40 and a_over <= 40:
                    lines.append('📊 兩隊近期小分趨勢明顯，本場小分機率較高。')
        except Exception as e:
            print(f'[Analyzer] Team history error: {e}')

    # 9. 球員狀態（NBA 傷兵 / MLB 先發投手）
    if game.get('status') == 'upcoming':
        try:
            if is_bball and get_nba_team_injuries:
                for team_nm, is_home in [(home_name, True), (away_name, False)]:
                    injuries = get_nba_team_injuries(team_nm)
                    if not injuries:
                        continue
                    out_list = [i for i in injuries if i['status'] == 'Out']
                    doubtful_list = [i for i in injuries if i['status'] == 'Doubtful']
                    q_list = [i for i in injuries if i['status'] in ('Questionable', 'Day-To-Day')]
                    parts = []
                    if out_list:
                        names = '、'.join(p['player'] for p in out_list[:4])
                        parts.append(f'確定缺陣：{names}')
                    if doubtful_list:
                        names = '、'.join(p['player'] for p in doubtful_list[:2])
                        parts.append(f'極可能缺陣：{names}')
                    if q_list:
                        names = '、'.join(p['player'] for p in q_list[:2])
                        parts.append(f'觀察名單：{names}')
                    if parts:
                        lines.append(f'【{team_nm} 傷兵】' + '　'.join(parts))
                    impact = calc_injury_impact(injuries)
                    if impact <= -8:
                        lines.append(f'⚠️ {team_nm} 多名主力傷缺，嚴重影響輪換深度！')
                    elif impact <= -4:
                        lines.append(f'⚠️ {team_nm} 有重要球員傷缺，陣容完整性受衝擊。')
                    if is_home:
                        home_adj += impact   # impact 為負，主隊傷情 → 主隊扣分
                    else:
                        away_adj += impact   # 客隊傷情 → 客隊扣分

            elif sport == 'baseball' and game.get('leagueId') == '1' and get_mlb_game_pitchers:
                game_date = game.get('date', '').replace('-', '')
                pitchers = get_mlb_game_pitchers(away_name, home_name, game_date)
                if pitchers:
                    home_p = pitchers.get('home')
                    away_p = pitchers.get('away')
                    pitcher_parts = []
                    if home_p and home_p.get('name'):
                        era = home_p.get('era', '')
                        w, l = home_p.get('wins', ''), home_p.get('losses', '')
                        rec = f'{w}勝{l}敗 ' if w and l else ''
                        era_text = f'ERA {era}' if era else 'ERA —'
                        pitcher_parts.append(f'主：{home_p["name"]}（{rec}{era_text}）')
                        home_adj += calc_pitcher_quality(home_p)
                    if away_p and away_p.get('name'):
                        era = away_p.get('era', '')
                        w, l = away_p.get('wins', ''), away_p.get('losses', '')
                        rec = f'{w}勝{l}敗 ' if w and l else ''
                        era_text = f'ERA {era}' if era else 'ERA —'
                        pitcher_parts.append(f'客：{away_p["name"]}（{rec}{era_text}）')
                        away_adj += calc_pitcher_quality(away_p)
                    if pitcher_parts:
                        lines.append('【先發投手】' + '　'.join(pitcher_parts))
                    h_q = calc_pitcher_quality(home_p) if home_p else 0
                    a_q = calc_pitcher_quality(away_p) if away_p else 0
                    if h_q - a_q >= 5:
                        lines.append(f'⚾ {home_name} 先發投手優勢明顯，防守端較有把握。')
                    elif a_q - h_q >= 5:
                        lines.append(f'⚾ {away_name} 先發投手更為強勢，客隊投手戰佔優。')

                # Yahoo 球場天氣（賽前/進行中時影響大小分預測）
                if get_mlb_game_info and expected_total > 0:
                    try:
                        yahoo_info = get_mlb_game_info(away_name, home_name)
                        if yahoo_info and yahoo_info.get('temp_c') is not None:
                            temp_c = yahoo_info['temp_c']
                            venue = yahoo_info.get('venue', '')
                            weather_text = format_weather_text(temp_c, venue)
                            if weather_text:
                                lines.append(weather_text)
                            w_impact = calc_weather_impact(temp_c)
                            expected_total += w_impact  # 調整預估總分
                    except Exception:
                        pass
        except Exception as e:
            print(f'[Analyzer] Player data factor error: {e}')

    # 沒有任何數據
    if not lines:
        if is_neutral:
            lines.append(f'本場比賽 {home_name} vs {away_name}，中立場地作賽，無主客場優勢。')
        else:
            lines.append(f'本場比賽 {home_name}（主）迎戰 {away_name}（客），主隊擁有主場優勢。')
        lines.append('建議關注兩隊近期傷病動態與輪休情況，作為投注參考依據。')

    # 9. 總結
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
        'expectedTotal': expected_total,
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

    # 盤口資訊（用於推薦計算）
    odds = game.get('odds', {})

    # 快速推薦 — 始終用賽前分析邏輯（避免比分改變推薦結果）
    if game.get('status') in ('finished', 'live'):
        pre_game = dict(game)
        pre_game['status'] = 'upcoming'
        pre_game['homeScore'] = None
        pre_game['awayScore'] = None
        analysis = generate_analysis(pre_game, sport)
    else:
        analysis = generate_analysis(game, sport)
    hw = analysis['homeWin']
    aw = analysis['awayWin']
    diff = abs(hw - aw)
    fav = home if hw >= aw else away
    dog = away if hw >= aw else home
    exp_total = analysis.get('expectedTotal', 0)

    try:
        spread_val = float(odds.get('spread', '0'))
    except (ValueError, TypeError):
        spread_val = 0
    abs_spread = abs(spread_val)
    is_unsigned = odds.get('spread_unsigned', False)

    # 盤口讓分方向判斷：
    #   有正負號（playsport）：spread < 0 → 主隊讓，spread > 0 → 客隊讓
    #   無正負號（livescore）：由分析勝率推斷誰是讓分方
    if abs_spread > 0 and not is_unsigned:
        # playsport 簽名盤口
        if spread_val < 0:
            spread_fav = home   # 主隊讓分（主隊強）
            spread_dog = away
        else:
            spread_fav = away   # 客隊讓分（客隊強）
            spread_dog = home
    elif abs_spread > 0 and is_unsigned:
        # livescore 無符號盤口：用分析勝率推斷方向
        spread_fav = fav    # 分析看好的隨是讓分方
        spread_dog = dog
    else:
        spread_fav = fav
        spread_dog = dog

    # ─── 取得實際賠率 ────────────────────────────────────────────
    soccer_odds = None
    if sport == 'soccer' and game.get('status') != 'finished' and get_soccer_game_odds:
        try:
            soccer_odds = get_soccer_game_odds(away, home)
        except Exception:
            pass

    real_odds = None
    if game.get('status') != 'finished':
        if sport == 'soccer' and soccer_odds:
            real_odds = {
                'ml_home':    soccer_odds.get('ml_home'),
                'ml_away':    soccer_odds.get('ml_away'),
                'ml_draw':    soccer_odds.get('ml_draw'),
                'over_odds':  soccer_odds.get('over_odds'),
                'under_odds': soccer_odds.get('under_odds'),
                'total_line': soccer_odds.get('ou_line'),
            }
        elif get_game_odds:
            try:
                real_odds = get_game_odds(game)
            except Exception:
                pass

    # ─── 三合一推薦 + 個別 EV ────────────────────────────────────
    is_finished = game.get('status') == 'finished' and game.get('homeScore') is not None

    def _ev_str(prob, odds_val):
        ev = (prob * odds_val - 1) * 100
        sign = '+' if ev >= 0 else ''
        return f'📈 EV {sign}{ev:.1f}%'

    def _win_odds_est(p):
        if p >= 0.75: return 1.25
        if p >= 0.68: return 1.45
        if p >= 0.60: return 1.65
        if p >= 0.54: return 1.82
        return 1.91

    rec_lines = []

    # ── 1. 獨贏 ──────────────────────────────────────────────────
    win_side = home if hw >= aw else away
    win_prob  = max(hw, aw) / 100
    if real_odds and real_odds.get('ml_home') and real_odds.get('ml_away'):
        win_odds_used = real_odds['ml_home'] if win_side == home else real_odds['ml_away']
    else:
        win_odds_used = _win_odds_est(win_prob)

    if is_finished:
        hs_int, as_int = int(game['homeScore']), int(game['awayScore'])
        actual_winner = home if hs_int > as_int else (away if as_int > hs_int else None)
        mark = '✅' if win_side == actual_winner else ('➖' if actual_winner is None else '❌')
        rec_lines.append(f'🔮 獨贏：{win_side} {mark}')
    else:
        rec_lines.append(f'🔮 獨贏：{win_side}　{_ev_str(win_prob, win_odds_used)}')

    # ── 2. 讓分盤（足球用 Asian handicap）──────────────────────
    if sport == 'soccer':
        if abs_spread > 0:
            spread_label = f'{spread_fav} 讓{abs_spread:g}球'
            cover_prob = min(win_prob * 0.90, 0.80)
            is_home_cover = (spread_fav == home)
        else:
            spread_label = f'{win_side} 讓0球'
            cover_prob = win_prob * 0.88
            is_home_cover = (win_side == home)
        sp_odds_used = (real_odds.get('spread_odds', 1.88)
                        if real_odds and real_odds.get('spread_odds') else 1.88)
        if is_finished:
            hs_int, as_int = int(game['homeScore']), int(game['awayScore'])
            sd = hs_int - as_int
            if abs_spread > 0:
                mark = '✅' if (sd > abs_spread if is_home_cover else -sd > abs_spread) else '❌'
            else:
                mark = ('✅' if (sd > 0 if is_home_cover else sd < 0)
                        else ('➖' if sd == 0 else '❌'))
            rec_lines.append(f'🔮 讓分：{spread_label} {mark}')
        else:
            rec_lines.append(f'🔮 讓分：{spread_label}　{_ev_str(cover_prob, sp_odds_used)}')
    elif abs_spread > 0:
        if diff > 10 and fav == spread_fav:
            rec_spread   = spread_fav
            spread_label = f'{spread_fav} 讓{abs_spread:.1f}'
            cover_prob   = min(win_prob * 0.93, 0.85)
        else:
            rec_spread   = spread_dog
            spread_label = f'{spread_dog} 受讓{abs_spread:.1f}'
            cover_prob   = min((1 - win_prob) + 0.08, 0.75)
        sp_odds_used = real_odds.get('spread_odds', 1.91) if real_odds and real_odds.get('spread_odds') else 1.91
        if is_finished:
            hs_int, as_int = int(game['homeScore']), int(game['awayScore'])
            score_diff = hs_int - as_int
            if rec_spread == spread_fav:
                covered = (score_diff > abs_spread) if spread_fav == home else (-score_diff > abs_spread)
            else:
                covered = (score_diff + abs_spread > 0) if spread_dog == home else (-score_diff + abs_spread > 0)
            mark = '✅' if covered else '❌'
            rec_lines.append(f'🔮 讓分：{spread_label} {mark}')
        else:
            rec_lines.append(f'🔮 讓分：{spread_label}　{_ev_str(cover_prob, sp_odds_used)}')
    elif not is_finished:
        rec_lines.append(f'🔮 讓分：無盤口')

    # ── 3. 大小分 ────────────────────────────────────────────────
    ou_line_val = 0.0
    if real_odds and real_odds.get('total_line'):
        ou_line_val = float(real_odds['total_line'])
    elif exp_total > 0:
        ou_line_val = float(round(exp_total / 5) * 5)

    # 足球：確保永遠有 ou_line_val 和 exp_total（避免「數據不足」）
    if sport == 'soccer':
        if ou_line_val == 0:
            ou_line_val = 2.5  # 世界盃/國際賽預設盤口線
        if exp_total == 0:
            draw_pct = analysis.get('draw', 25)
            lean = -0.25 if draw_pct > 28 else (0.1 if diff > 25 else -0.15)
            exp_total = ou_line_val + lean

    if ou_line_val > 0 and exp_total > 0:
        dist_pct = abs(exp_total - ou_line_val) / max(ou_line_val, 1)
        ou_prob  = min(0.50 + dist_pct * 1.5, 0.80)
        if exp_total >= ou_line_val:
            ou_side      = '大'
            ou_odds_used = real_odds.get('over_odds', 1.88) if real_odds and real_odds.get('over_odds') else 1.88
        else:
            ou_side      = '小'
            ou_odds_used = real_odds.get('under_odds', 1.88) if real_odds and real_odds.get('under_odds') else 1.88
        if is_finished:
            hs_int, as_int = int(game['homeScore']), int(game['awayScore'])
            total_score = hs_int + as_int
            hit = (ou_side == '大' and total_score > ou_line_val) or (ou_side == '小' and total_score < ou_line_val)
            mark = '✅' if hit else '❌'
            rec_lines.append(f'🔮 大小：{ou_side} {ou_line_val:g} {mark}')
        else:
            rec_lines.append(f'🔮 大小：{ou_side} {ou_line_val:g}　{_ev_str(ou_prob, ou_odds_used)}')
    elif not is_finished:
        rec_lines.append(f'🔮 大小：數據不足')

    # ── 4. 波膽（足球 Poisson 預測）─────────────────────────────
    if sport == 'soccer':
        sox_exp = exp_total if exp_total > 0 else 2.3
        tw = hw + aw
        lam_h = sox_exp * hw / tw if tw > 0 else sox_exp / 2
        lam_a = sox_exp * aw / tw if tw > 0 else sox_exp / 2
        ps_best = (1, 0)
        ps_best_p = 0.0
        for bhg in range(6):
            for bag in range(6):
                p = _poisson_prob(bhg, lam_h) * _poisson_prob(bag, lam_a)
                if p > ps_best_p:
                    ps_best_p = p
                    ps_best = (bhg, bag)
        bhg, bag = ps_best
        if is_finished:
            hs_int, as_int = int(game['homeScore']), int(game['awayScore'])
            mark = '✅' if hs_int == bhg and as_int == bag else '❌'
            rec_lines.append(f'🔮 波膽：主{bhg}客{bag} {mark}')
        else:
            prob_pct = round(ps_best_p * 100, 1)
            rec_lines.append(f'🔮 波膽：主{bhg}客{bag}　({prob_pct}%)')

    # ─── 組合輸出 ────────────────────────────────────────────────
    is_neutral = game.get('neutral', False)
    prefix = '🔷' if is_neutral else '🚌'
    suffix = '🔶' if is_neutral else '🏠'
    lines = [
        f'━━━━━━━━━━━━━━━',
        f'{status}  {time_str}',
        f'{prefix} {away}',
        f'📊 {score}',
        f'{suffix} {home}',
    ]
    lines.extend(rec_lines)
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

    is_neutral = game.get('neutral', False)
    h_icon = '🔶' if is_neutral else '🏠'
    a_icon = '🔷' if is_neutral else '🚌'

    lines = [
        f'⚡ 賽事分析',
        f'━━━━━━━━━━━━━━━',
        f'{h_icon} {home}',
        f'{a_icon} {away}',
        f'',
        f'📈 勝率預測',
        f'{h_icon} {h_bar} {hw}%',
    ]

    if sport != 'basketball':
        dw = analysis['draw']
        d_bar = '█' * round(dw / 100 * bar_len)
        lines.append(f'◎ {d_bar} {dw}%')

    lines.extend([
        f'{a_icon} {a_bar} {aw}%',
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


def format_league_games_text(games, league_name, sport='basketball', date_str=''):
    """
    格式化單一聯賽的比賽為 LINE 訊息
    """
    sport_emoji = {
        'basketball': '🏀', 'baseball': '⚾',
        'soccer': '⚽', 'hockey': '🏒', 'tennis': '🎾'
    }.get(sport, '🏆')

    if not games:
        return (
            f'{sport_emoji} {league_name}\n'
            f'━━━━━━━━━━━━━━━\n'
            f'📅 {date_str}\n\n'
            f'該聯賽目前無賽事資料。'
        )

    lines = [
        f'{sport_emoji} {league_name}',
        f'━━━━━━━━━━━━━━━',
    ]
    if date_str:
        lines.append(f'📅 {date_str}')
    lines.append(f'📊 共 {len(games)} 場賽事')
    lines.append('')

    for g in games:
        lines.append(format_game_text(g, sport))

    lines.append('')
    lines.append(f'👇 點擊下方按鈕查看詳細分析')
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
        f'{sport_emoji} Yahoo 賽事',
        f'━━━━━━━━━━━━━━━',
    ]
    if date_str:
        lines.append(f'📅 {date_str}')
    lines.append(f'📊 共 {len(games)} 場賽事')
    lines.append('')

    for league, league_games in groups.items():
        lines.append(f'\n🏆── {league}【{len(league_games)} 場】──')
        for g in league_games:
            lines.append(format_game_text(g, sport))
        lines.append('')

    if len(games) > 11:
        lines.append(f'👇 點擊按鈕或輸入「分析 隊名」查看詳細分析')
    else:
        lines.append(f'👇 點擊下方按鈕查看詳細分析')
    return '\n'.join(lines)


def format_yesterday_results(games, sport='basketball', date_str='', title='昨日賽果'):
    """
    格式化賽果結算：只顯示已結束的比賽，標記推薦是否命中
    """
    sport_emoji = {
        'basketball': '🏀', 'baseball': '⚾',
        'soccer': '⚽', 'hockey': '🏒', 'tennis': '🎾'
    }.get(sport, '🏆')

    # 只取已結束的比賽
    finished = [g for g in games if g.get('status') == 'finished']

    if not finished:
        return (
            f'{sport_emoji} {title}\n'
            f'━━━━━━━━━━━━━━━\n'
            f'📅 {date_str}\n\n'
            f'無已結束的賽事資料。'
        )

    hit = 0
    total_rec = 0
    result_lines = []

    for game in finished:
        home = game.get('home', '—')
        away = game.get('away', '—')
        hs = game.get('homeScore')
        a_s = game.get('awayScore')
        time_str = game.get('time', '')

        if hs is not None and a_s is not None:
            score = f'{a_s} : {hs}'
            hs_int, as_int = int(hs), int(a_s)
            winner = home if hs_int > as_int else (away if as_int > hs_int else None)
        else:
            score = '— : —'
            winner = None

        # 計算推薦（始終用賽前分析，避免比分翻轉推薦）
        odds = game.get('odds', {})
        pre_game = dict(game)
        pre_game['status'] = 'upcoming'
        pre_game['homeScore'] = None
        pre_game['awayScore'] = None
        analysis = generate_analysis(pre_game, sport)
        hw = analysis['homeWin']
        aw = analysis['awayWin']
        diff = abs(hw - aw)
        fav = home if hw >= aw else away

        try:
            spread_val = float(odds.get('spread', '0'))
        except (ValueError, TypeError):
            spread_val = 0
        abs_spread = abs(spread_val)

        # 推薦類型與文字
        rec_type = 'win'  # 預設獨贏
        if diff > 20 and abs_spread > 0:
            rec_text = f'{fav} 讓 {abs_spread} 分'
            rec_type = 'spread_fav'
        elif diff > 10:
            rec_text = f'{fav} 獨贏'
        elif spread_val != 0:
            dog_team = away if spread_val > 0 else home
            rec_text = f'{dog_team} 受讓 {abs_spread} 分'
            rec_type = 'spread_dog'
        else:
            rec_text = f'{fav} 獨贏'

        # 判斷是否過盤
        total_rec += 1
        if hs is not None and a_s is not None:
            hs_int, as_int = int(hs), int(a_s)
            score_diff = hs_int - as_int  # 主隊 - 客隊

            if rec_type == 'spread_fav':
                # 讓分盤：推薦方讓分後是否仍贏
                if fav == home:
                    covered = (score_diff - abs_spread) > 0
                else:
                    covered = (-score_diff - abs_spread) > 0
            elif rec_type == 'spread_dog':
                # 受讓盤：受讓方加分後是否贏
                dog_team = away if spread_val > 0 else home
                if dog_team == home:
                    covered = (score_diff + abs_spread) > 0
                else:
                    covered = (-score_diff + abs_spread) > 0
            else:
                # 獨贏盤：推薦方是否贏球
                covered = (fav == home and score_diff > 0) or (fav == away and score_diff < 0)

            if covered:
                mark = '✅'
                hit += 1
            else:
                mark = '❌'
        else:
            mark = '➖'
            total_rec -= 1

        is_neutral = game.get('neutral', False)
        h_icon = '🔶' if is_neutral else '🏠'
        a_icon = '🔷' if is_neutral else '🚌'
        result_lines.append(f'━━━━━━━━━━━━━━━')
        result_lines.append(f'⏰ {time_str}')
        result_lines.append(f'{a_icon} {away}')
        result_lines.append(f'📊 {score}')
        result_lines.append(f'{h_icon} {home}')
        result_lines.append(f'🔮 推薦：{rec_text}  {mark}')

    rate = round(hit / total_rec * 100, 1) if total_rec > 0 else 0

    header = [
        f'{sport_emoji} {title}結算',
        f'━━━━━━━━━━━━━━━',
        f'📅 {date_str}',
        f'📊 共 {len(finished)} 場已結束',
        f'🎯 命中：{hit}/{total_rec}（{rate}%）',
    ]

    return '\n'.join(header + [''] + result_lines)
