"""
會員管理系統
- 管理員管理
- 序號生成與兌換
- 會員到期檢查
"""
import os
import json
import string
import random
from datetime import datetime, timedelta, timezone

TW_TZ = timezone(timedelta(hours=8))

# 資料檔案路徑
DATA_FILE = os.path.join(os.path.dirname(__file__), 'data.json')

# 序號有效期限選項（分鐘）
DURATION_OPTIONS = {
    '30分鐘': 30,
    '1小時': 60,
    '1天': 1440,
    '7天': 10080,
    '30天': 43200,
}

# ===== 資料存取 =====

def _load_data():
    """讀取 JSON 資料檔"""
    default = {
        'admins': [],       # list of uid strings
        'codes': {},        # code -> {duration_min, created_by, created_at, used_by, used_at}
        'members': {},      # uid -> {expires_at}
    }
    if not os.path.exists(DATA_FILE):
        return default
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 確保所有 key 都存在
        for k in default:
            if k not in data:
                data[k] = default[k]
        return data
    except Exception:
        return default


def _save_data(data):
    """寫入 JSON 資料檔"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[Membership] save error: {e}')


# ===== 管理員 =====

def is_admin(uid):
    """檢查是否為管理員"""
    data = _load_data()
    return uid in data['admins']


def add_admin(uid):
    """新增管理員"""
    data = _load_data()
    if uid not in data['admins']:
        data['admins'].append(uid)
        _save_data(data)
        return True
    return False


def remove_admin(uid):
    """移除管理員"""
    data = _load_data()
    if uid in data['admins']:
        data['admins'].remove(uid)
        _save_data(data)
        return True
    return False


def get_admin_list():
    """取得管理員列表"""
    data = _load_data()
    return data['admins']


# ===== 序號 =====

def _generate_code_str():
    """生成 XXXX-XXXX-XXXX 格式序號"""
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(random.choices(chars, k=4)) for _ in range(3)]
    return '-'.join(parts)


def generate_code(admin_uid, duration_label):
    """
    管理員生成序號
    duration_label: '30分鐘' | '1小時' | '1天' | '7天' | '30天'
    回傳: (code_str, duration_min) 或 (None, None)
    """
    if duration_label not in DURATION_OPTIONS:
        return None, None

    duration_min = DURATION_OPTIONS[duration_label]
    data = _load_data()

    # 確保序號不重複
    code = _generate_code_str()
    while code in data['codes']:
        code = _generate_code_str()

    now = datetime.now(TW_TZ).isoformat()
    data['codes'][code] = {
        'duration_min': duration_min,
        'duration_label': duration_label,
        'created_by': admin_uid,
        'created_at': now,
        'used_by': None,
        'used_at': None,
    }
    _save_data(data)
    return code, duration_min


def redeem_code(uid, code):
    """
    用戶兌換序號
    回傳: (success: bool, message: str)
    """
    code = code.strip().upper()
    data = _load_data()

    if code not in data['codes']:
        return False, '❌ 序號無效，請確認後再試。'

    code_info = data['codes'][code]

    if code_info['used_by'] is not None:
        return False, '❌ 此序號已被使用。'

    # 標記序號已使用
    now = datetime.now(TW_TZ)
    code_info['used_by'] = uid
    code_info['used_at'] = now.isoformat()

    # 計算會員到期時間
    duration_min = code_info['duration_min']
    # 如果用戶已有會員且未過期，從到期時間延長
    if uid in data['members']:
        try:
            current_expires = datetime.fromisoformat(data['members'][uid]['expires_at'])
            if current_expires > now:
                new_expires = current_expires + timedelta(minutes=duration_min)
            else:
                new_expires = now + timedelta(minutes=duration_min)
        except Exception:
            new_expires = now + timedelta(minutes=duration_min)
    else:
        new_expires = now + timedelta(minutes=duration_min)

    data['members'][uid] = {
        'expires_at': new_expires.isoformat(),
    }

    _save_data(data)

    # 格式化到期時間
    expires_str = new_expires.strftime('%Y/%m/%d %H:%M')
    return True, (
        f'✅ 儲值成功！\n\n'
        f'▸ 序號：{code}\n'
        f'▸ 時長：{code_info["duration_label"]}\n'
        f'▸ 到期：{expires_str}\n\n'
        f'現在可以使用賽事查詢和分析功能了！'
    )


# ===== 會員 =====

def is_member_active(uid):
    """檢查用戶會員是否有效"""
    # 管理員永遠有效
    if is_admin(uid):
        return True

    data = _load_data()
    if uid not in data['members']:
        return False

    try:
        expires = datetime.fromisoformat(data['members'][uid]['expires_at'])
        now = datetime.now(TW_TZ)
        return now < expires
    except Exception:
        return False


def get_member_expiry(uid):
    """取得會員到期時間，回傳格式化字串"""
    if is_admin(uid):
        return '♾️ 管理員（永久有效）'

    data = _load_data()
    if uid not in data['members']:
        return None

    try:
        expires = datetime.fromisoformat(data['members'][uid]['expires_at'])
        now = datetime.now(TW_TZ)
        expires_str = expires.strftime('%Y/%m/%d %H:%M')

        if now >= expires:
            return f'❌ 已過期（{expires_str}）'

        # 計算剩餘時間
        diff = expires - now
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60

        if days > 0:
            remain = f'{days}天{hours}小時'
        elif hours > 0:
            remain = f'{hours}小時{minutes}分鐘'
        else:
            remain = f'{minutes}分鐘'

        return f'✅ 有效至 {expires_str}（剩餘 {remain}）'
    except Exception:
        return None


def get_all_codes(admin_uid):
    """取得所有序號列表（管理員用）"""
    data = _load_data()
    return data['codes']
