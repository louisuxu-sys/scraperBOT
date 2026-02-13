"""
會員管理系統（Firebase Firestore 版）
- 管理員管理
- 序號生成與兌換
- 會員到期檢查
"""
import os
import json
import string
import random
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import credentials, firestore

TW_TZ = timezone(timedelta(hours=8))

# ===== Firebase 初始化 =====

_FIREBASE_CRED_JSON = os.environ.get('FIREBASE_CREDENTIALS', '')

_firebase_ok = False

if _FIREBASE_CRED_JSON:
    print(f'[Firebase] FIREBASE_CREDENTIALS found, length={len(_FIREBASE_CRED_JSON)}')
    try:
        cred_dict = json.loads(_FIREBASE_CRED_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        _firebase_ok = True
        print('[Firebase] ✅ Initialized from env var')
    except Exception as e:
        print(f'[Firebase] ❌ Failed to parse FIREBASE_CREDENTIALS: {e}')
        print(f'[Firebase] First 50 chars: {_FIREBASE_CRED_JSON[:50]}')

if not _firebase_ok:
    # 本地開發：從檔案讀取
    cred_path = os.path.join(os.path.dirname(__file__), 'firebase-key.json')
    if os.path.exists(cred_path):
        try:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            _firebase_ok = True
            print('[Firebase] ✅ Initialized from firebase-key.json')
        except Exception as e:
            print(f'[Firebase] ❌ Failed to load firebase-key.json: {e}')

if not _firebase_ok:
    print('[Firebase] ⚠️ No credentials found! Firestore will not work.')

db = firestore.client() if _firebase_ok else None

# 永久管理員（從環境變數讀取）
_ENV_ADMINS = [uid.strip() for uid in os.environ.get('ADMIN_UIDS', '').split(',') if uid.strip()]

# 序號有效期限選項（分鐘）
DURATION_OPTIONS = {
    '30分鐘': 30,
    '1小時': 60,
    '1天': 1440,
    '7天': 10080,
    '30天': 43200,
}


# ===== 管理員 =====

def is_admin(uid):
    """檢查是否為管理員（環境變數 + Firestore）"""
    if uid in _ENV_ADMINS:
        return True
    if not db:
        return False
    doc = db.collection('admins').document(uid).get()
    return doc.exists


def add_admin(uid):
    """新增管理員"""
    if not db or is_admin(uid):
        return False
    db.collection('admins').document(uid).set({'created_at': datetime.now(TW_TZ).isoformat()})
    return True


def remove_admin(uid):
    """移除管理員（環境變數管理員無法移除）"""
    if uid in _ENV_ADMINS or not db:
        return False
    doc = db.collection('admins').document(uid).get()
    if doc.exists:
        db.collection('admins').document(uid).delete()
        return True
    return False


def get_admin_list():
    """取得全部管理員列表"""
    if not db:
        return list(_ENV_ADMINS)
    docs = db.collection('admins').stream()
    db_admins = [doc.id for doc in docs]
    return list(set(_ENV_ADMINS + db_admins))


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
    if duration_label not in DURATION_OPTIONS or not db:
        return None, None

    duration_min = DURATION_OPTIONS[duration_label]

    # 確保序號不重複
    code = _generate_code_str()
    while db.collection('codes').document(code).get().exists:
        code = _generate_code_str()

    now = datetime.now(TW_TZ).isoformat()
    db.collection('codes').document(code).set({
        'duration_min': duration_min,
        'duration_label': duration_label,
        'created_by': admin_uid,
        'created_at': now,
        'used_by': None,
        'used_at': None,
    })
    return code, duration_min


def redeem_code(uid, code):
    """
    用戶兌換序號
    回傳: (success: bool, message: str)
    """
    code = code.strip().upper()
    if not db:
        return False, '❌ 系統維護中，請稍後再試。'
    doc_ref = db.collection('codes').document(code)
    doc = doc_ref.get()

    if not doc.exists:
        return False, '❌ 序號無效，請確認後再試。'

    code_info = doc.to_dict()

    if code_info.get('used_by') is not None:
        return False, '❌ 此序號已被使用。'

    # 標記序號已使用
    now = datetime.now(TW_TZ)
    doc_ref.update({
        'used_by': uid,
        'used_at': now.isoformat(),
    })

    # 計算會員到期時間
    duration_min = code_info['duration_min']
    member_doc = db.collection('members').document(uid).get()

    if member_doc.exists:
        try:
            current_expires = datetime.fromisoformat(member_doc.to_dict()['expires_at'])
            if current_expires > now:
                new_expires = current_expires + timedelta(minutes=duration_min)
            else:
                new_expires = now + timedelta(minutes=duration_min)
        except Exception:
            new_expires = now + timedelta(minutes=duration_min)
    else:
        new_expires = now + timedelta(minutes=duration_min)

    db.collection('members').document(uid).set({
        'expires_at': new_expires.isoformat(),
    })

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
    if not db:
        return False

    doc = db.collection('members').document(uid).get()
    if not doc.exists:
        return False

    try:
        expires = datetime.fromisoformat(doc.to_dict()['expires_at'])
        now = datetime.now(TW_TZ)
        return now < expires
    except Exception:
        return False


def get_member_expiry(uid):
    """取得會員到期時間，回傳格式化字串"""
    if is_admin(uid):
        return '♾️ 管理員（永久有效）'
    if not db:
        return None

    doc = db.collection('members').document(uid).get()
    if not doc.exists:
        return None

    try:
        expires = datetime.fromisoformat(doc.to_dict()['expires_at'])
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
    if not db:
        return {}
    docs = db.collection('codes').stream()
    return {doc.id: doc.to_dict() for doc in docs}
