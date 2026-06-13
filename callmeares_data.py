# -*- coding: utf-8 -*-
"""
callmeares 博彩平台賠率整合模組
GraphQL API: https://queennew-demo.callmeares.com/api
HK 賠率 → 歐洲小數賠率：decimal = HK + 1

凍結邏輯：
  - 比賽未開始（upcoming）：即時拉取並快取
  - 比賽已開始/結束（live/finished）：回傳賽前凍結值，不再更新
  - 若賽前盤口異動 ≥ 0.5，產生警示供前端顯示
"""
import os
import time
import threading
import requests

# ── 認證設定（可透過環境變數覆蓋）────────────────────────────
ENDPOINT      = os.environ.get("CALLMEARES_ENDPOINT", "https://queennew-demo.callmeares.com/api")
AUTH_BASE     = "https://sportsbook-auth.callmeares.com"
AUTH_ENDPOINT = f"{AUTH_BASE}/api/OddsApi/getOddsApiToken"

# Bearer JWT 初始值（從 env var 或 hardcode）
# bot 啟動後由 keepalive 執行緒自動定期 renew，不需手動更新
_INITIAL_BEARER_JWT = os.environ.get(
    "CALLMEARES_BEARER_JWT",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1lIjoiNTA0MjR5eV9HVDNNMDAwMDI4IiwiV2ViSWQiOiI1MDQyNCIsIkZwSWQiOiI4MjEiLCJMYW5ndWFnZSI6IlpIX1RXIiwiQ3VycmVuY3kiOiJUTVAiLCJDdXN0SWQiOiIxNjE5OTE0IiwiRG93bmxpbmVJZHMiOiJbMTYyNTYwOSwxNjI0OTEwLDE1MTA0MDNdIiwiZXhwIjoxNzgxNDM4NjY0LCJpc3MiOiJzcG9ydHMucGxheWVyU2l0ZSIsImF1ZCI6IlNwb3J0c1NlcnZpY2UifQ"
    ".9uDK22YDswzEjYHMX9BX4WJw8j9ygJV85xABT5HxEFg"
)

# 執行期可變 JWT（由 renew 更新）
_bearer_jwt      = _INITIAL_BEARER_JWT
_bearer_jwt_lock = threading.Lock()

def _get_bearer_jwt():
    with _bearer_jwt_lock:
        return _bearer_jwt

def _set_bearer_jwt(new_jwt):
    global _bearer_jwt
    with _bearer_jwt_lock:
        _bearer_jwt = new_jwt

URL_TOKEN  = os.environ.get("CALLMEARES_URL_TOKEN", "36079777.TlQNDSnVVlsctCtVhMAchjV.821")
LOGIN_NAME = os.environ.get("CALLMEARES_LOGIN",     "6979e92ba82ee28a5b9392912e9f30a5")
USER_CODE  = os.environ.get("CALLMEARES_USER",      "50424yy_GT3M000028")

ORIGIN  = "https://sports-ares-demo.callmeares.com"
REFERER = (
    f"https://sports-ares-demo.callmeares.com/?token={URL_TOKEN}"
    f"&domain=sports-demo-x01.callmeares.com"
    f"&loginname={LOGIN_NAME}"
    f"&device=d&lang=ZH_TW&oddstyle=HK&theme=neoares"
    f"&oddsmode=Double&da=da&u={USER_CODE}&in=2"
)

_HEADERS = {
    "Content-Type":    "application/json",
    "Origin":          ORIGIN,
    "Referer":         REFERER,
    "Accept":          "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
}

def _auth_headers():
    """帶入最新 Bearer JWT 的 HTTP headers。"""
    return {
        **_HEADERS,
        "Authorization": f"Bearer {_get_bearer_jwt()}",
        "Accept": "application/json, text/plain, */*",
    }

# ── GQL token 自動刷新 ───────────────────────────────────────
_gql_token    = None
_gql_token_ts = 0.0
GQL_TOKEN_TTL = 3600  # 1 小時重新換一次


def _refresh_gql_token():
    """用 Bearer JWT 向 getOddsApiToken 換取 GQL token，成功後啟動 keepalive。
    若 JWT 失效（errorCode 非 0）則觸發 Playwright 自動重新登入。
    """
    global _gql_token, _gql_token_ts
    try:
        r = requests.get(AUTH_ENDPOINT, headers=_auth_headers(), timeout=10)
        data = r.json()
        if data.get("errorCode") == 0 and data.get("result"):
            _gql_token    = data["result"]
            _gql_token_ts = time.time()
            print("[Callmeares] GQL token 刷新成功")
            _start_keepalive()
            return _gql_token
        else:
            print(f"[Callmeares] GQL token 刷新失敗: {data}，嘗試自動重新登入")
            return _try_auto_login_then_refresh()
    except Exception as e:
        print(f"[Callmeares] getOddsApiToken error: {e}")
    return None


def _try_auto_login_then_refresh():
    """JWT 失效時呼叫 Playwright 自動登入，成功後重試 GQL token。"""
    global _gql_token, _gql_token_ts
    try:
        from callmeares_login import auto_login_and_get_jwt
        new_jwt = auto_login_and_get_jwt()
        if not new_jwt:
            return None
        _set_bearer_jwt(new_jwt)
        # 用新 JWT 再試一次
        r = requests.get(AUTH_ENDPOINT, headers=_auth_headers(), timeout=10)
        data = r.json()
        if data.get("errorCode") == 0 and data.get("result"):
            _gql_token    = data["result"]
            _gql_token_ts = time.time()
            print("[Callmeares] 自動重新登入後 GQL token 刷新成功")
            _start_keepalive()
            return _gql_token
        else:
            print(f"[Callmeares] 自動登入後仍無法刷新 GQL token: {data}")
    except Exception as e:
        print(f"[Callmeares] 自動登入流程錯誤: {e}")
    return None


def _get_gql_token():
    """取得有效的 GQL token（必要時自動刷新）。"""
    if _gql_token and time.time() - _gql_token_ts < GQL_TOKEN_TTL:
        return _gql_token
    return _refresh_gql_token()


# ── Session keepalive（heartbeat + JWT renewal）──────────────
HEARTBEAT_INTERVAL = 55        # 每 55 秒送一次心跳（瀏覽器是 60 秒）
RENEWAL_INTERVAL   = 25 * 60   # 每 25 分鐘 renew JWT（瀏覽器是 30 分鐘）

_keepalive_thread = None
_keepalive_lock   = threading.Lock()


def _send_heartbeat():
    try:
        r = requests.post(f"{AUTH_BASE}/api/Auth/heartbeat",
                          json={}, headers=_auth_headers(), timeout=10)
        return r.status_code == 200 and r.json().get("errorCode") == 0
    except Exception:
        return False


def _renew_bearer_jwt():
    try:
        r = requests.post(f"{AUTH_BASE}/api/Auth/renew",
                          json={}, headers=_auth_headers(), timeout=10)
        data = r.json()
        if data.get("errorCode") == 0:
            new_jwt = (data.get("result") or {}).get("sessionId")
            if new_jwt:
                _set_bearer_jwt(new_jwt)
                # 強制下次 GQL token 刷新也用新 JWT
                global _gql_token_ts
                _gql_token_ts = 0.0
                print("[Callmeares] Bearer JWT renewed")
                return True
    except Exception:
        pass
    return False


def _keepalive_loop():
    last_renewal = time.time()
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        _send_heartbeat()
        if time.time() - last_renewal >= RENEWAL_INTERVAL:
            if _renew_bearer_jwt():
                last_renewal = time.time()


def _start_keepalive():
    """啟動 keepalive daemon thread（僅啟動一次）。"""
    global _keepalive_thread
    with _keepalive_lock:
        if _keepalive_thread is None or not _keepalive_thread.is_alive():
            _keepalive_thread = threading.Thread(
                target=_keepalive_loop, daemon=True, name="callmeares-keepalive")
            _keepalive_thread.start()
            print("[Callmeares] Session keepalive 已啟動")

# ── GraphQL persisted query hashes ───────────────────────────
HASH_EVENTS = "abe0a8efb3ed9c1f7f39914e114b421f08e88147f796370ff79aad020ba41589"
HASH_ODDS   = "9bbe56514106f28dfed931a3fc1942f1ba408223457333bafb3ddce1ffeeaa53"

# playsport sport 名稱 → callmeares sport 名稱
SPORT_MAP = {
    "basketball": "Basketball",
    "baseball":   "Baseball",
    "soccer":     "Soccer",
    "hockey":     "IceHockey",
    "tennis":     "Tennis",
}

EVENTS_TTL = 180   # EventsQuery 快取 3 分鐘
ODDS_TTL   = 120   # Odds 快取 2 分鐘
BATCH_SIZE = 20    # 每次 EventOddsQuery 批次數

# ── 內部快取 ─────────────────────────────────────────────────
_events_cache = {}   # sport -> (ts, {event_id: event_dict})
_odds_cache   = {}   # sport -> (ts, {(home_zh, away_zh): formatted_odds})
_pregame_odds = {}   # game_key -> formatted_odds（凍結，不過期）
_odds_alerts  = {}   # game_key -> 警示文字


# ── HK → Decimal 換算 ────────────────────────────────────────
def _hk2dec(hk):
    try:
        return round(float(hk) + 1.0, 3)
    except (TypeError, ValueError):
        return None


# ── HTTP 工具 ────────────────────────────────────────────────
def _post(payload, timeout=10):
    try:
        r = requests.post(ENDPOINT, json=payload, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[Callmeares] API error: {e}")
        return None


# ── EventsQuery：拿事件清單（隊名 + event ID）───────────────
def _fetch_events_raw(sport="soccer"):
    sport_name = SPORT_MAP.get(sport, sport.capitalize())
    payload = [{
        "operationName": "EventsQuery",
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": HASH_EVENTS}},
        "variables": {"query": {
            "sport":         sport_name,
            "filter":        {"presetFilter": "All", "date": "All"},
            "lang":          "ZH_TW",
            "oddsCategory":  "All",
            "popularRank":   0,
            "timeZone":      "UTC_8",
            "token":         _get_gql_token() or "",
        }},
    }]
    data = _post(payload)
    if not data:
        return {}

    events = {}
    for item in data:
        if not item:
            continue
        d = item.get("data") or {}
        ev_list = (d.get("events") or d.get("sportEvents") or
                   d.get("eventList") or [])
        for ev in ev_list:
            eid = ev.get("id") or ev.get("eventId")
            if not eid:
                continue
            home_t = (ev.get("homeTeam") or {}).get("teamName", "")
            away_t = (ev.get("awayTeam") or {}).get("teamName", "")
            events[int(eid)] = {
                "home":       home_t,
                "away":       away_t,
                "tournament": (ev.get("tournament") or {}).get("tournamentName", ""),
                "startTime":  ev.get("periodStartTime") or ev.get("startTime", ""),
            }
    return events


def fetch_events(sport="soccer"):
    cached = _events_cache.get(sport)
    if cached and time.time() - cached[0] < EVENTS_TTL:
        return cached[1]
    events = _fetch_events_raw(sport)
    _events_cache[sport] = (time.time(), events)
    return events


# ── EventOddsQuery：批次取得賠率 ────────────────────────────
def _parse_odds_list(odds_list):
    """eventOdds 陣列 → {handicap, ou, 1x2}"""
    out = {}
    for o in odds_list:
        mt     = o.get("marketType", "")
        point  = o.get("point", 0)
        prices = {p["option"]: p["price"] for p in o.get("prices", [])}

        if mt == "Handicap" and "handicap" not in out:
            out["handicap"] = {
                "point":     point,
                "home_odds": _hk2dec(prices.get("h")),
                "away_odds": _hk2dec(prices.get("a")),
            }
        elif mt == "OverUnder" and "ou" not in out:
            out["ou"] = {
                "line":       point,
                "over_odds":  _hk2dec(prices.get("h")),
                "under_odds": _hk2dec(prices.get("a")),
            }
        elif mt == "_1X2" and "1x2" not in out:
            out["1x2"] = {
                "home_odds": _hk2dec(prices.get("1")),
                "draw_odds": _hk2dec(prices.get("X")),
                "away_odds": _hk2dec(prices.get("2")),
            }
    return out or None


def fetch_event_odds(event_ids):
    """批次取得 event 賠率，回傳 {event_id: parsed_dict}"""
    if not event_ids:
        return {}
    result = {}
    for i in range(0, len(event_ids), BATCH_SIZE):
        chunk = event_ids[i:i + BATCH_SIZE]
        tok = _get_gql_token() or ""
        payload = [{
            "operationName": "EventOddsQuery",
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": HASH_ODDS}},
            "variables": {"query": {
                "id": eid, "filter": "All", "marketFilter": "All", "token": tok,
            }},
        } for eid in chunk]
        data = _post(payload)
        if not data:
            continue
        for idx, item in enumerate(data):
            if idx >= len(chunk):
                break
            if not item:
                continue
            parsed = _parse_odds_list((item.get("data") or {}).get("eventOdds", []))
            if parsed:
                result[chunk[idx]] = parsed
    return result


# ── 隊名模糊匹配 ─────────────────────────────────────────────
_STRIP_SUFFIXES = ["隊", " FC", " SC", " CF", " F.C.", " United", " City"]


def _strip(name):
    n = name.strip()
    for s in _STRIP_SUFFIXES:
        n = n.replace(s, "")
    return n.strip()


def _name_match(n1, n2):
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    s1, s2 = _strip(n1), _strip(n2)
    if s1 == s2:
        return True
    # 子字串（一方包含另一方）
    # 閾值降為 2，以支援 MLB/NBA 2 字短名（如「雙城」「洋基」「騎士」）
    min_len = min(len(s1), len(s2))
    if min_len >= 2 and (s1 in s2 or s2 in s1):
        return True
    # 前4字元相同
    if len(s1) >= 4 and len(s2) >= 4 and s1[:4] == s2[:4]:
        return True
    return False


# ── 整批預取（一個運動的所有賠率）──────────────────────────
def _format_odds(raw):
    """raw {handicap?, ou?, 1x2?} → 標準 odds dict"""
    if not raw:
        return None
    out = {"source": "callmeares"}

    hc = raw.get("handicap")
    if hc:
        out["spread_val"]  = hc["point"]      # 主隊視角：負=讓分，正=受讓
        # 讓分賠率：讓分方那一側的賠率
        out["spread_odds"] = (hc["home_odds"] if (hc["point"] or 0) <= 0
                              else hc["away_odds"])

    ou = raw.get("ou")
    if ou:
        out["total_line"]  = ou["line"]
        out["over_odds"]   = ou["over_odds"]
        out["under_odds"]  = ou["under_odds"]

    x12 = raw.get("1x2")
    if x12:
        out["ml_home"] = x12["home_odds"]
        out["ml_away"] = x12["away_odds"]
        out["ml_draw"] = x12["draw_odds"]

    return out if len(out) > 1 else None


def _prefetch_sport(sport):
    """預取某運動全部事件+賠率，存入 _odds_cache"""
    events   = fetch_events(sport)
    if not events:
        return {}
    odds_map = fetch_event_odds(list(events.keys()))
    result   = {}
    for eid, ev in events.items():
        raw = odds_map.get(eid)
        if raw:
            fmt = _format_odds(raw)
            if fmt:
                result[(ev["home"], ev["away"])] = fmt
    _odds_cache[sport] = (time.time(), result)
    return result


def _get_sport_odds(sport):
    cached = _odds_cache.get(sport)
    if cached and time.time() - cached[0] < ODDS_TTL:
        return cached[1]
    return _prefetch_sport(sport)


# ── 盤口異動偵測 ─────────────────────────────────────────────
def _check_change(old, new):
    alerts = []
    for field, label in [("spread_val", "讓分"), ("total_line", "大小分")]:
        o = old.get(field) or 0
        n = new.get(field) or 0
        if abs(o - n) >= 0.5:
            alerts.append(f"{label} {o:+g}→{n:+g}" if field == "spread_val"
                          else f"{label} {o:g}→{n:g}")
    return ("⚠️ 賽前盤口異動：" + "、".join(alerts)) if alerts else None


# ── 公開介面 ─────────────────────────────────────────────────
def _game_key(game):
    return f"{game.get('home','')}|{game.get('away','')}|{game.get('date','')}"


def _lookup_odds(sport, home_zh, away_zh):
    """在預取賠率中查找（精確 → 模糊 → 主客互換）"""
    all_odds = _get_sport_odds(sport)

    # 正向查找
    odds = all_odds.get((home_zh, away_zh))
    if odds is None:
        for (h, a), v in all_odds.items():
            if _name_match(h, home_zh) and _name_match(a, away_zh):
                odds = v
                break

    if odds is not None:
        return odds

    # 主客互換查找（playsport 與 callmeares 主客定義不同時）
    swapped = all_odds.get((away_zh, home_zh))
    if swapped is None:
        for (h, a), v in all_odds.items():
            if _name_match(h, away_zh) and _name_match(a, home_zh):
                swapped = v
                break

    if swapped is None:
        return None

    # 修正方向：讓分視角翻轉，ML 主客互換
    fixed = dict(swapped)
    if fixed.get("spread_val") is not None:
        fixed["spread_val"] = -fixed["spread_val"]
    fixed["ml_home"], fixed["ml_away"] = fixed.get("ml_away"), fixed.get("ml_home")
    return fixed


def get_game_odds(game):
    """
    取得比賽的實際賠率，替換 playsport_data.get_game_odds。

    - upcoming：即時拉取並存入 _pregame_odds；偵測盤口異動
    - live / finished：回傳凍結的賽前賠率，不再更新

    game dict 需包含 '_sport'（由 analyzer.format_game_text 注入）。
    回傳格式（與 playsport_data 相容）：
      {spread_val, spread_odds, total_line, over_odds, under_odds,
       ml_home, ml_away, ml_draw, source}
    """
    sport  = game.get("_sport", "basketball")
    key    = _game_key(game)
    status = game.get("status", "upcoming")

    if status in ("live", "finished"):
        return _pregame_odds.get(key)   # None 代表比賽前從未查詢過

    home_zh = game.get("home", "")
    away_zh = game.get("away", "")
    odds    = _lookup_odds(sport, home_zh, away_zh)

    if odds:
        existing = _pregame_odds.get(key)
        if existing:
            alert = _check_change(existing, odds)
            if alert:
                _odds_alerts[key] = alert
        _pregame_odds[key] = odds

    return odds


def get_soccer_game_odds(away_zh, home_zh, gameday="today"):
    """
    足球賠率（向後相容 playsport_data 介面）。
    回傳 {ml_home, ml_away, ml_draw, over_odds, under_odds, ou_line}
    """
    odds = _lookup_odds("soccer", home_zh, away_zh)
    if not odds:
        return None
    result = {}
    for src, dst in [("ml_home", "ml_home"), ("ml_away", "ml_away"),
                     ("ml_draw", "ml_draw"), ("over_odds", "over_odds"),
                     ("under_odds", "under_odds")]:
        if odds.get(src):
            result[dst] = odds[src]
    if odds.get("total_line"):
        result["ou_line"] = odds["total_line"]
    return result or None


def get_odds_alert(game):
    """回傳此場次的賽前盤口異動警示（None 代表無異動）"""
    return _odds_alerts.get(_game_key(game))
