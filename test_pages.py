# -*- coding: utf-8 -*-
"""快速驗證：盤口方向 + emoji 顯示"""
from scraper import fetch_all_games
from analyzer import format_game_text

games = fetch_all_games('basketball')
nba = [g for g in games if 'NBA' in g.get('league', '')]
print(f"NBA {len(nba)} 場\n")
for g in nba[:3]:
    print(format_game_text(g, 'basketball'))
    print()
