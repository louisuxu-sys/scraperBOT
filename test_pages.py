# -*- coding: utf-8 -*-
"""Verify full flow: WBC + NBA status detection"""
from scraper import fetch_all_games
from analyzer import format_game_text

for sport, label in [('baseball', '棒球'), ('basketball', '籃球')]:
    games = fetch_all_games(sport)
    finished = sum(1 for g in games if g['status'] == 'finished')
    upcoming = sum(1 for g in games if g['status'] == 'upcoming')
    live = sum(1 for g in games if g['status'] == 'live')
    print(f"\n{label}: {len(games)} games (finished={finished}, upcoming={upcoming}, live={live})")
    for g in games[:3]:
        print(format_game_text(g, sport))
        print()
