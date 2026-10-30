import json
from urllib.request import Request, urlopen
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

st.set_page_config(page_title='旋律驗證 V2.2｜單秘密雙回合', page_icon='🎵', layout='centered')
MUSIC_DIR = Path(__file__).resolve().parent / 'music'
ROUND_SIZE = 11
LOCK_SECONDS = 25
MIN_OVERLAP = 3
MAX_OVERLAP = 5


def initial_state():
    defaults = {
        'phase': 'register', 'secret': None, 'round_orders': {}, 'overlap_count': None,
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
    """Retry unsent events; retain them in session until delivered."""
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


def make_round_orders(files, secret):
    """Exactly 3-5 distinct songs overlap across 11-song rounds, including secret."""
    rng = secrets.SystemRandom()
    names = [p.name for p in files]
    pool = [name for name in names if name != secret]
    if len(pool) < 18:
        raise ValueError('至少需要19首不同歌曲，才能控制兩輪的重複歌曲數。')
    first_decoys = rng.sample(pool, ROUND_SIZE - 1)
    overlap_count = rng.randint(MIN_OVERLAP, MAX_OVERLAP)
    shared_decoys = rng.sample(first_decoys, overlap_count - 1)
    remaining = [name for name in pool if name not in first_decoys]
    second_decoys = shared_decoys + rng.sample(remaining, ROUND_SIZE - overlap_count)
    first = first_decoys + [secret]
    second = second_decoys + [secret]
    rng.shuffle(first)
    rng.shuffle(second)
    assert len(first) == len(second) == ROUND_SIZE
    assert len(set(first) & set(second)) == overlap_count
    return {1: first, 2: second}, overlap_count


def start_round(number):
    s = st.session_state
    s['round'] = number
    s.order = s.round_orders[number]
    s['index'] = 0
    s.round_started = time.monotonic()
    s.started = time.monotonic()
    s.phase = 'verify'


def fail(reason):
    s = st.session_state
    s.phase = 'result'
    s.result = False
    s.reason = f'第 {s["round"]} 輪失敗：{reason}'
    s.lock_until = time.monotonic() + LOCK_SECONDS


def handle(answer):
    s = st.session_state
    filename = s.order[s['index']]
    correct = s.secret
    if answer == 'YES':
        if filename != correct:
            record(answer, filename, 'failed_wrong_confirmation')
            fail('確認了錯誤旋律。')
        else:
            record(answer, filename, 'round_passed')
            if s['round'] == 1:
                s.phase = 'round1_passed'
            else:
                s.phase = 'result'
                s.result = True
                s.reason = '第一輪及第二輪皆成功。'
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



initial_state()
st.markdown("""<style>
    .block-container {max-width: 850px; padding-top: 1.4rem;}
    h1 {font-size: 2.2rem !important;}
    h2, h3 {font-size: 1.85rem !important;}
    div.stButton > button {min-height: 3.5rem; font-size: 1.15rem; font-weight: 700;}
    .round-banner {font-size: 2.0rem; font-weight: 800; padding: 16px;
      border-radius: 12px; background: rgba(120,120,120,.12); margin: 14px 0;}
    .big-status {font-size: 2rem; font-weight: 800; padding: 18px;
      border-radius: 12px; margin: 14px 0;}
    .good {border: 2px solid #2c9b65;}
    .bad {border: 2px solid #d85a5a;}
    .song-number {font-size: 1.5rem; font-weight: 700;}
</style>""", unsafe_allow_html=True)
st.title('🎵 旋律式身分驗證 V2.2')
st.caption('研究原型｜單一秘密旋律｜兩輪各11首｜跨輪重複3至5首（含秘密旋律）')
st.info('Email僅供研究識別，不驗證信箱所有權；本研究原型不適用於正式帳號登入。')

files = sorted(p for p in MUSIC_DIR.glob('*.mp3') if p.is_file()) if MUSIC_DIR.exists() else []
st.write(f'目前讀取到 **{len(files)} 首 MP3**（資料夾：`music/`）')
if len(files) < 19:
    st.error('至少需要19首MP3才能執行本研究設定；原本20首曲庫即可直接使用。')
    st.stop()

s = st.session_state
if s.phase == 'register':
    st.subheader('第一步｜輸入識別資料並選擇一首秘密旋律')
    email = st.text_input('受試者Email／代碼（僅作研究識別）', value=s.user_email,
                          placeholder='請輸入Email或受試者代碼').strip()
    consent = st.checkbox('我同意研究系統記錄識別資料、旋律判斷、反應時間及驗證結果，用於研究分析。')
    st.caption('研究紀錄將傳送至研究者管理的Google Sheets；請勿使用真實帳號密碼。')
    names = [p.name for p in files]
    secret = st.selectbox('請選擇一首秘密旋律（兩輪皆使用）', names,
                          index=None, placeholder='請選擇秘密旋律')
    with st.expander('試聽秘密旋律'):
        if secret:
            st.audio((MUSIC_DIR / secret).read_bytes(), format='audio/mpeg')
    if st.button('完成註冊並開始第一輪', type='primary', use_container_width=True,
                 disabled=not secret or not email or not consent):
        s.user_email = email
        s.secret = secret
        s.round_orders, s.overlap_count = make_round_orders(files, secret)
        s.logs = []
        s.pending_logs = []
        s.synced_count = 0
        s.sync_error = ''
        s.session_id = secrets.token_hex(8)
        s.result = None
        s.lock_until = 0.0
        start_round(1)
        st.rerun()

elif s.phase == 'verify':
    st.markdown(f'<div class="round-banner">第 {s["round"]} 輪驗證｜共 2 輪</div>',
                unsafe_allow_html=True)
    st.progress((s['index'] + 1) / ROUND_SIZE)
    st.markdown(f'<div class="song-number">第 {s["index"] + 1}／{ROUND_SIZE} 首</div>',
                unsafe_allow_html=True)
    st.write('請先播放旋律，再判斷是否為註冊時選擇的秘密旋律。')
    filename = s.order[s['index']]
    st.audio((MUSIC_DIR / filename).read_bytes(), format='audio/mpeg')
    left, right = st.columns(2)
    with left:
        if st.button('YES｜這是我的旋律', type='primary', use_container_width=True):
            handle('YES')
            sync_pending_logs()
            st.rerun()
    with right:
        if st.button('NO｜不是這首', use_container_width=True):
            handle('NO')
            sync_pending_logs()
            st.rerun()
    st.caption('每首只判斷一次；誤按YES或跳過秘密旋律會立即判定該輪失敗。')

elif s.phase == 'round1_passed':
    st.markdown('<div class="big-status good">✅ 第一輪驗證成功！</div>',
                unsafe_allow_html=True)
    st.subheader('準備進入第二輪驗證')
    st.write('第二輪仍須辨認同一首秘密旋律。兩輪有部分歌曲重複。')
    if st.button('開始第二輪驗證', type='primary', use_container_width=True):
        start_round(2)
        st.rerun()

else:
    if s.result:
        st.markdown('<div class="big-status good">🎉 驗證成功：兩輪皆通過！</div>',
                    unsafe_allow_html=True)
        st.success('第一輪成功、第二輪成功。')
    else:
        st.markdown(f'<div class="big-status bad">❌ 第 {s["round"]} 輪驗證失敗</div>',
                    unsafe_allow_html=True)
        st.error(s.reason)
        st.warning('本次失敗後須等待25秒才能重新開始。')
    if s.pending_logs:
        st.warning(f'尚有 {len(s.pending_logs)} 筆紀錄未同步至Google Sheets，請點選下方按鈕重試同步。')
        if st.button('重試同步未上傳紀錄'):
            sync_pending_logs()
            st.rerun()
        if s.get('sync_error'):
            st.caption(f'同步錯誤：{s.sync_error}')
    elif s.logs:
        st.success(f'已向Google Sheets送出 {s.synced_count} 筆操作紀錄。')
    @st.fragment(run_every="1s")
    def cooldown_and_retry():
        # 只更新倒數區域，不重新執行整個應用或重複送出 Log。
        seconds = max(0, int(s.lock_until - time.monotonic() + 0.999))
        if seconds:
            st.markdown(f"### ⏳ 安全冷卻倒數：{seconds} 秒")
            st.progress((LOCK_SECONDS - seconds) / LOCK_SECONDS)
            st.caption("倒數會每秒自動更新；結束後即可重新驗證。")
        elif not s.result:
            st.success("✅ 25秒冷卻結束，可以重新驗證。")

        if st.button('保留密碼，從第一輪重新驗證',
                     use_container_width=True, disabled=seconds > 0):
            # 保留原密碼與受試者識別；重新抽樣兩輪歌曲。
            s.round_orders, s.overlap_count = make_round_orders(files, s.secret)
            s.session_id = secrets.token_hex(8)
            s.result = None
            s.reason = ''
            s.lock_until = 0.0
            s.logs = []
            # 保留 pending_logs，避免未同步紀錄遺失。
            start_round(1)
            st.rerun()

    cooldown_and_retry()

st.divider()
st.caption('研究Log僅由系統同步至研究者管理的Google Sheets，不提供使用者下載。'
           '本研究原型的25秒限制僅作用於目前Session，非跨裝置的正式帳號鎖定。')
