import csv
import io
import json
from urllib.request import Request, urlopen
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

st.set_page_config(page_title='旋律驗證 V2｜雙秘密雙回合', page_icon='🎵', layout='centered')
MUSIC_DIR = Path(__file__).resolve().parent / 'music'
ROUND_SIZE = 11
LOCK_SECONDS = 25


def initial_state():
    defaults = {
        'phase': 'register', 'secret_a': None, 'secret_b': None,
        'round': 0, 'order': [], 'index': 0, 'logs': [],
        'started': None, 'session_id': None, 'lock_until': 0.0,
        'result': None, 'reason': '', 'round_started': None,
        'user_email': '', 'pending_logs': [], 'synced_count': 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def send_log(row):
    """Send one log event from the Streamlit server, not the participant's browser."""
    url = st.secrets.get("GOOGLE_SCRIPT_URL", "")
    token = st.secrets.get("GOOGLE_WRITE_TOKEN", "")
    if not url or not token:
        return False, "尚未設定 Google Sheets 連線資訊"

    payload = {
        "token": token,
        "timestamp": row["timestamp_utc"],
        "user_email": row["user_email"],
        "session_id": row["session_id"],
        "round": row["round"],
        "step": row["step"],
        "track_id": row["track_id"],
        "answer": row["answer"],
        "rt_sec": row["rt_sec"],
        "round_elapsed_sec": row["round_elapsed_sec"],
        "outcome": row["outcome"],
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("ok") is True:
            return True, ""
        return False, str(result.get("error", "Google Sheets 拒絕寫入"))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def sync_pending_logs():
    """Retry unsent events without deleting the local CSV backup."""
    s = st.session_state
    remaining = []
    for i, row in enumerate(s.pending_logs):
        ok, error = send_log(row)
        if ok:
            s.synced_count += 1
        else:
            remaining = s.pending_logs[i:]
            s.sync_error = error
            break
    s.pending_logs = remaining
    if not remaining:
        s.sync_error = ""


def record(action, filename, outcome='continue'):
    now = time.monotonic()
    row = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'user_email': st.session_state.user_email,
        'session_id': st.session_state.session_id,
        'round': st.session_state['round'],
        'step': st.session_state['index'] + 1,
        'track_id': Path(filename).stem,
        'answer': action,
        'rt_sec': round(now - st.session_state.started, 3) if st.session_state.started is not None else None,
        'round_elapsed_sec': round(now - st.session_state.round_started, 3) if st.session_state.round_started is not None else None,
        'outcome': outcome,
    }
    st.session_state.logs.append(row)
    st.session_state.pending_logs.append(row)


def start_round(number, files):
    secret = st.session_state.secret_a if number == 1 else st.session_state.secret_b
    # Keep the other secret out of this round's decoy set.
    other = st.session_state.secret_b if number == 1 else st.session_state.secret_a
    pool = [p.name for p in files if p.name not in (secret, other)]
    decoys = secrets.SystemRandom().sample(pool, ROUND_SIZE - 1)
    challenge = decoys + [secret]
    secrets.SystemRandom().shuffle(challenge)
    st.session_state['round'] = number
    st.session_state.order = challenge
    st.session_state['index'] = 0
    st.session_state.round_started = time.monotonic()
    st.session_state.started = time.monotonic()
    st.session_state.phase = 'verify'


def fail(reason):
    st.session_state.phase = 'result'
    st.session_state.result = False
    st.session_state.reason = reason
    st.session_state.lock_until = time.monotonic() + LOCK_SECONDS


def handle(answer, files):
    s = st.session_state
    filename = s.order[s['index']]
    correct = s.secret_a if s['round'] == 1 else s.secret_b
    if answer == 'YES':
        if filename != correct:
            record(answer, filename, 'failed_wrong_confirmation')
            fail('確認了錯誤旋律。')
        else:
            record(answer, filename, 'round_passed')
            if s['round'] == 1:
                start_round(2, files)
            else:
                s.phase = 'result'
                s.result = True
                s.reason = '兩輪皆成功。'
    else:
        if filename == correct:
            record(answer, filename, 'failed_skipped_secret')
            fail('跳過了正確旋律。')
        else:
            record(answer, filename, 'skipped_decoy')
            s['index'] += 1
            if s['index'] >= ROUND_SIZE:
                fail('本輪未確認正確旋律。')
            else:
                s.started = time.monotonic()


def csv_bytes(rows):
    if not rows:
        return b''
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig')


initial_state()
st.title('🎵 旋律式身分驗證 V2.1')
st.caption('研究原型｜20首曲庫｜雙秘密 A/B｜每輪11首｜兩輪皆通過才成功')
st.info('研究原型：Email僅供受試者識別，不驗證信箱所有權；本系統不適用於真實帳號登入。')

files = sorted(p for p in MUSIC_DIR.glob('*.mp3') if p.is_file()) if MUSIC_DIR.exists() else []
st.write(f'目前讀取到 **{len(files)} 首 MP3**（資料夾：`music/`）')
if len(files) != 20:
    st.error('請在專案的 music/ 資料夾放入剛好20首MP3，然後重新整理頁面。')
    st.stop()

s = st.session_state
if s.phase == 'register':
    st.subheader('第一步｜輸入Email並註冊兩首秘密旋律')
    email = st.text_input('受試者Email（僅作研究識別）', value=s.user_email, placeholder='請輸入Email或受試者代碼').strip()
    consent = st.checkbox('我同意研究系統記錄Email、旋律判斷、反應時間及驗證結果，用於研究分析。')
    st.caption('Email僅供研究識別，不驗證格式或信箱所有權；資料將傳送至研究者管理的Google Sheets。')
    names = [p.name for p in files]
    a = st.selectbox('秘密旋律 A（第一輪）', names, index=None, placeholder='請選擇A')
    b = st.selectbox('秘密旋律 B（第二輪）', names, index=None, placeholder='請選擇B')
    with st.expander('試聽選取的旋律'):
        if a:
            st.write('A')
            st.audio((MUSIC_DIR / a).read_bytes(), format='audio/mpeg')
        if b:
            st.write('B')
            st.audio((MUSIC_DIR / b).read_bytes(), format='audio/mpeg')
    email_valid = bool(email)
    if st.button('完成註冊並開始驗證', type='primary',
                 disabled=not a or not b or a == b or not email_valid or not consent):
        s.user_email = email
        s.secret_a, s.secret_b = a, b
        s.logs = []
        s.pending_logs = []
        s.synced_count = 0
        s.sync_error = ''
        s.session_id = secrets.token_hex(8)
        s.result = None
        start_round(1, files)
        st.rerun()
    if email and not email_valid:
        st.warning('請輸入Email或受試者代碼。')
    if a and b and a == b:
        st.warning('A與B必須是不同的旋律。')

elif s.phase == 'verify':
    st.subheader(f'第二步｜第 {s["round"]} 輪驗證')
    st.progress((s['index'] + 1) / ROUND_SIZE)
    st.write(f'第 **{s["index"] + 1} / {ROUND_SIZE}** 首：請先播放，然後判斷是否為本輪秘密旋律。')
    filename = s.order[s['index']]
    # Do not show the filename or target position during verification.
    st.audio((MUSIC_DIR / filename).read_bytes(), format='audio/mpeg')
    left, right = st.columns(2)
    with left:
        if st.button('YES｜這是我的旋律', type='primary', use_container_width=True):
            handle('YES', files)
            sync_pending_logs()
            st.rerun()
    with right:
        if st.button('NO｜跳過', use_container_width=True):
            handle('NO', files)
            sync_pending_logs()
            st.rerun()
    st.caption('按錯YES或跳過正解會立即失敗；不會提示正解位置。')

else:
    if s.result:
        st.success('驗證成功：兩輪均通過。')
    else:
        st.error(f'驗證失敗：{s.reason}')
        st.warning('本次失敗後設定25秒冷卻期。')
    if s.pending_logs:
        st.warning(f'尚有 {len(s.pending_logs)} 筆紀錄未成功同步至Google Sheets。請保留CSV備份。')
        if st.button('重試同步未上傳紀錄'):
            sync_pending_logs()
            st.rerun()
        if s.get('sync_error'):
            st.caption(f'同步錯誤：{s.sync_error}')
    elif s.logs:
        st.success(f'已向Google Sheets送出 {s.synced_count} 筆操作紀錄。')
    if s.logs:
        st.download_button('下載本次實驗紀錄 CSV', data=csv_bytes(s.logs),
                           file_name=f'melodic_v2_{s.session_id}.csv', mime='text/csv')
    remaining = max(0, int(s.lock_until - time.monotonic() + 0.999))
    if remaining:
        st.write(f'目前仍在冷卻期，約 **{remaining} 秒**。時間到後請點選下方按鈕。')
    if st.button('重新註冊／開始新一次測試'):
        if time.monotonic() < s.lock_until:
            st.warning('冷卻期尚未結束，請稍後再試。')
        else:
            s.phase = 'register'
            s.secret_a = None
            s.secret_b = None
            s.logs = []
            st.rerun()

st.divider()
st.caption('實驗Log會嘗試同步至研究者的Google Sheets；若同步失敗，請下載CSV備份。Email為自行填寫，未經信箱驗證。')
