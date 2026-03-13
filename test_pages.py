# -*- coding: utf-8 -*-
"""Test: EV display for all sports"""
from scraper import fetch_all_games
from analyzer import format_game_text

for sport, label in [('basketball', '籃球'), ('baseball', '棒球')]:
    games = fetch_all_games(sport)
    with_odds = [g for g in games if g.get('odds_api')]
    show = with_odds[:3]
    print(f"\n=== {label} ({len(with_odds)} 場有盤口) ===\n")
    for g in show:
        print(format_game_text(g, sport))
        print()
