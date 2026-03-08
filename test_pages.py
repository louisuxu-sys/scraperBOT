# -*- coding: utf-8 -*-
"""Verify fetch_all_games for baseball returns correct WBC status"""
from scraper import fetch_all_games
from analyzer import format_game_text

games = fetch_all_games('baseball', '20260308')
wbc = [g for g in games if 'WBC' in g.get('league', '') or '經典賽' in g.get('league', '')]
print(f"WBC: {len(wbc)} games\n")
for g in wbc:
    text = format_game_text(g, 'baseball')
    print(text)
    print()
