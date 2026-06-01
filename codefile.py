import streamlit as st
import gspread
import pandas as pd
import time
import hashlib
import secrets
from google.oauth2.service_account import Credentials
from datetime import datetime

st.set_page_config(
    page_title="SNS Platform",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =========================
# Google Sheets 연결 함수
# =========================
@st.cache_resource
def connect_google_sheets():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scope
    )

    client = gspread.authorize(creds)
    spreadsheet_id = st.secrets["google_sheets"]["spreadsheet_id"]
    spreadsheet = client.open_by_key(spreadsheet_id)

    return spreadsheet

# =========================
# Google Sheets 워크시트 객체 캐시
# spreadsheet.worksheet(...)가 매번 실행되면 API 읽기 제한에 걸리므로 캐시 처리
# =========================
@st.cache_resource
def get_worksheets(_spreadsheet):
    return {
        "users": _spreadsheet.worksheet("users"),
        "posts": _spreadsheet.worksheet("posts"),
        "comments": _spreadsheet.worksheet("comments"),
        "reports": _spreadsheet.worksheet("reports"),
        "groups": _spreadsheet.worksheet("groups"),
        "likes": _spreadsheet.worksheet("likes")
    }
# =========================
# 시트 데이터를 DataFrame으로 변환
# Google Sheets API 읽기 요청을 줄이기 위해 임시 캐시 사용
# =========================
def sheet_to_df(sheet, cache_key, ttl_seconds=120):
    now = time.time()

    if cache_key in st.session_state:
        cached = st.session_state[cache_key]

        if now - cached["time"] < ttl_seconds:
            return cached["data"]

    data = sheet.get_all_records()
    df = pd.DataFrame(data)

    st.session_state[cache_key] = {
        "time": now,
        "data": df
    }

    return df


# =========================
# 시트 수정 후 캐시 삭제 함수
# =========================
def clear_sheet_cache(*cache_keys):
    for cache_key in cache_keys:
        if cache_key in st.session_state:
            del st.session_state[cache_key]


# =========================
# 위험도 분류 함수
# =========================
def classify_risk(text):
    text = text.replace(" ", "").lower()

    block_keywords = [
        "자살", "죽고싶", "죽고싶다", "죽여", "살해", "테러", "폭탄"
    ]

    review_keywords = [
        "우울", "괴롭", "힘들", "왕따", "따돌림", "협박", "학폭"
    ]

    support_keywords = [
        "외롭", "불안", "고민", "스트레스", "속상", "걱정"
    ]

    for word in block_keywords:
        if word in text:
            return "BLOCK"

    for word in review_keywords:
        if word in text:
            return "REVIEW"

    for word in support_keywords:
        if word in text:
            return "SUPPORT"

    return "SAFE"


# =========================
# 위험도에 따른 게시물 상태 결정
# =========================
def decide_status(risk_level):
    if risk_level in ["SAFE", "SUPPORT"]:
        return "공개"
    elif risk_level == "REVIEW":
        return "검토대기"
    elif risk_level == "BLOCK":
        return "차단"
    else:
        return "검토대기"
# =========================
# 비밀번호 해시 생성 및 검증 함수
# 실제 비밀번호를 그대로 저장하지 않고, salt와 hash만 저장
# =========================
def make_password_hash(password):
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

    return password_hash, salt


def verify_password(input_password, saved_password_hash, saved_password_salt):
    saved_password_hash = str(saved_password_hash).strip()
    saved_password_salt = str(saved_password_salt).strip()

    if saved_password_hash == "" or saved_password_salt == "":
        return False

    input_password_hash = hashlib.sha256(
        (saved_password_salt + input_password).encode("utf-8")
    ).hexdigest()

    return input_password_hash == saved_password_hash
# =========================
# 좋아요 수 증가 함수
# =========================
def increase_like(posts_sheet, post_id):
    records = posts_sheet.get_all_records()

    for index, record in enumerate(records):
        if record["post_id"] == post_id:
            sheet_row_number = index + 2

            current_likes = record["likes"]

            if current_likes == "":
                current_likes = 0

            current_likes = int(current_likes)
            new_likes = current_likes + 1

            posts_sheet.update_cell(sheet_row_number, 8, new_likes)

            return new_likes

    return None
# =========================
# 좋아요 1인 1회 제한 함수
# =========================
def add_like_once(likes_sheet, posts_sheet, users_sheet, post_id, post_writer_id, login_user_id):
    records = likes_sheet.get_all_records()

    for record in records:
        saved_post_id = str(record.get("post_id", ""))
        saved_user_id = str(record.get("user_id", ""))

        if saved_post_id == str(post_id) and saved_user_id == str(login_user_id):
            return False, "이미 좋아요를 누른 게시물입니다."

    like_id = "like_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    likes_sheet.append_row([
        like_id,
        post_id,
        login_user_id,
        created_at
    ])

    increase_like(posts_sheet, post_id)
    increase_user_points(users_sheet, post_writer_id, 1)
    clear_sheet_cache("likes", "posts", "users")

    return True, "좋아요가 반영되었습니다."
# =========================
# 사용자 포인트 증가 함수
# =========================
def increase_user_points(users_sheet, user_id, amount):
    records = users_sheet.get_all_records()

    for index, record in enumerate(records):
        if record["user_id"] == user_id:
            sheet_row_number = index + 2

            current_points = record["points"]

            if current_points == "":
                current_points = 0

            current_points = int(current_points)
            new_points = current_points + amount

            users_sheet.update_cell(sheet_row_number, 7, new_points)

            return new_points

    return None
# =========================
# 게시물 상태 변경 함수
# =========================
def update_post_status(posts_sheet, post_id, new_status):
    records = posts_sheet.get_all_records()

    for index, record in enumerate(records):
        if record["post_id"] == post_id:
            sheet_row_number = index + 2
            posts_sheet.update_cell(sheet_row_number, 7, new_status)
            clear_sheet_cache("posts")
            return True

    return False


# =========================
# 신고 처리 상태 변경 함수
# =========================
def update_report_status(reports_sheet, report_id, new_status):
    records = reports_sheet.get_all_records()

    for index, record in enumerate(records):
        if record["report_id"] == report_id:
            sheet_row_number = index + 2
            reports_sheet.update_cell(sheet_row_number, 5, new_status)
            clear_sheet_cache("reports")
            return True

    return False
# =========================
# 소그룹 자동 배정 함수
# =========================
def assign_group_to_user(groups_sheet, main_interest, user_id):
    records = groups_sheet.get_all_records()

    # 1. 같은 관심사의 기존 그룹이 있으면 인원 제한 없이 추가
    for index, record in enumerate(records):
        if record["main_interest"] == main_interest:
            member_ids_text = str(record["member_ids"])

            if member_ids_text.strip() == "":
                member_list = []
            else:
                member_list = [
                    member.strip()
                    for member in member_ids_text.split(",")
                    if member.strip() != ""
                ]

            if user_id not in member_list:
                member_list.append(user_id)

            new_member_ids = ", ".join(member_list)

            sheet_row_number = index + 2
            groups_sheet.update_cell(sheet_row_number, 4, new_member_ids)

            clear_sheet_cache("groups")

            return record["group_id"], record["group_name"]

    # 2. 같은 관심사의 그룹이 없으면 새 그룹 생성
    group_id = "group_" + datetime.now().strftime("%Y%m%d%H%M%S")
    group_name = main_interest + " 소그룹"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    groups_sheet.append_row([
        group_id,
        group_name,
        main_interest,
        user_id,
        created_at
    ])

    clear_sheet_cache("groups")

    return group_id, group_name
# =========================
# 로그인 상태 확인 함수
# =========================
def is_logged_in():
    return "login_user_id" in st.session_state and st.session_state["login_user_id"] != ""


def get_login_user_id():
    return st.session_state.get("login_user_id", "")


def get_login_nickname():
    return st.session_state.get("login_nickname", "")


def get_login_group_id():
    return st.session_state.get("login_group_id", "")
# =========================
# Google Sheets 연결
# =========================
spreadsheet = connect_google_sheets()
worksheets = get_worksheets(spreadsheet)

users_sheet = worksheets["users"]
posts_sheet = worksheets["posts"]
comments_sheet = worksheets["comments"]
reports_sheet = worksheets["reports"]
groups_sheet = worksheets["groups"]
likes_sheet = worksheets["likes"]

# =========================
# 전체 UI 디자인 CSS
# 댓글 작성 탭과 관리자 검토 탭은 코드상 유지하되 화면에서만 숨김
# =========================
st.markdown(
    """
    <style>
    /* 전체 배경 */
    .stApp {
        background: linear-gradient(180deg, #f7f9fc 0%, #eef3f8 100%);
    }

    /* 전체 화면 폭과 여백 */
    .block-container {
        max-width: 1050px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }

    /* 기본 글자 */
    html, body, [class*="css"] {
        font-family: "Pretendard", "Noto Sans KR", sans-serif;
        color: #1e293b;
    }

    /* 상단 기본 메뉴 숨김 */
    #MainMenu {
        visibility: hidden;
    }

    footer {
        visibility: hidden;
    }

    header {
        visibility: hidden;
        background: transparent;
    }

    /* 사이드바 디자인 */
    section[data-testid="stSidebar"] {
        background-color: #f1f5f9;
        border-right: 1px solid #dbe4ee;
    }

    section[data-testid="stSidebar"] h3 {
        color: #1f4f8f;
        font-weight: 800;
    }

    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] div {
        color: #334155;
        line-height: 1.6;
    }

    /* 사이드바 열기/닫기 버튼 보이게 하기 */
    div[data-testid="stSidebarCollapsedControl"] {
        visibility: visible !important;
        display: block !important;
    }

    button[kind="header"] {
        visibility: visible !important;
        display: inline-flex !important;
    }

    /* 댓글 작성 탭, 관리자 검토 탭 숨김 */
    #div[data-testid="stTabs"] button[data-baseweb="tab"]:nth-of-type(4),
    #div[data-testid="stTabs"] button[data-baseweb="tab"]:nth-of-type(5),
    #div[data-testid="stTabs"] button[data-baseweb="tab"]:nth-of-type(6) {
    #    display: none;
    #}

    /* 탭 전체 영역 */
    div[data-testid="stTabs"] {
        margin-top: 8px;
    }

    /* 탭 디자인 */
    div[data-testid="stTabs"] button[data-baseweb="tab"] {
        padding: 10px 18px;
        border-radius: 999px;
        margin-right: 6px;
        background-color: #ffffff;
        border: 1px solid #d8e0ea;
        color: #334155;
        font-weight: 700;
        box-shadow: 0 3px 10px rgba(15, 23, 42, 0.04);
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"]:hover {
        background-color: #e8f1ff;
        color: #1f4f8f;
        border: 1px solid #b8d4ff;
    }

    div[data-testid="stTabs"] button[aria-selected="true"] {
        background-color: #1f4f8f;
        color: white;
        border: 1px solid #1f4f8f;
        box-shadow: 0 5px 14px rgba(31, 79, 143, 0.22);
    }

    /* 버튼 디자인 */
    .stButton > button {
        border-radius: 999px;
        border: 1px solid #1f4f8f;
        background-color: #1f4f8f;
        color: white;
        font-weight: 700;
        padding: 0.45rem 1rem;
        white-space: nowrap;
        min-width: fit-content;
        box-shadow: 0 4px 12px rgba(31, 79, 143, 0.18);
    }

    .stButton > button:hover {
        border: 1px solid #163b6b;
        background-color: #163b6b;
        color: white;
        box-shadow: 0 6px 16px rgba(22, 59, 107, 0.24);
    }

    .stButton > button:active {
        transform: translateY(1px);
    }

    /* 입력창 */
    input, textarea {
        border-radius: 12px !important;
        border: 1px solid #cbd5e1 !important;
        background-color: #ffffff !important;
    }

    input:focus, textarea:focus {
        border: 1px solid #1f4f8f !important;
        box-shadow: 0 0 0 2px rgba(31, 79, 143, 0.12) !important;
    }

    /* selectbox 느낌 보강 */
    div[data-baseweb="select"] > div {
        border-radius: 12px !important;
        border-color: #cbd5e1 !important;
        background-color: #ffffff !important;
    }

    /* 구분선 */
    hr {
        margin-top: 1rem;
        margin-bottom: 1rem;
        border-color: #e2e8f0;
    }

    /* 카드 느낌 보강 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #ffffff;
        border-radius: 18px;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
        padding: 1.1rem 1.2rem;
        border: 1px solid #e2e8f0;
    }

    /* 카드 hover */
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        box-shadow: 0 12px 28px rgba(15, 23, 42, 0.09);
        border: 1px solid #cbd5e1;
    }

    /* 제목 */
    h1, h2, h3, h4, h5 {
        color: #1e293b;
        font-weight: 800;
    }

    /* 작은 설명 글 */
    .small-caption {
        color: #64748b;
        font-size: 0.92rem;
    }

    /* 카드 안의 보조 정보 */
    .post-meta {
        color: #64748b;
        font-size: 0.9rem;
    }

    /* 게시물 상태 배지 */
    .post-badge {
        display: inline-block;
        padding: 5px 12px;
        border-radius: 999px;
        background-color: #e8f1ff;
        color: #1f4f8f;
        font-size: 0.85rem;
        font-weight: 800;
        white-space: nowrap;
        margin-top: 6px;
    }

    /* 댓글 박스 */
    .comment-box {
        background-color: #f8fafc;
        border-left: 4px solid #dbeafe;
        padding: 10px 13px;
        border-radius: 12px;
        margin-bottom: 8px;
        border: 1px solid #e2e8f0;
    }

    .comment-box strong {
        color: #1f4f8f;
    }

    /* expander 디자인 */
    div[data-testid="stExpander"] {
        border-radius: 14px;
        border: 1px solid #dbe4ee;
        background-color: #ffffff;
    }

    .streamlit-expanderHeader {
        font-weight: 800;
        color: #1f4f8f;
    }

    /* metric 디자인 */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border-radius: 16px;
        padding: 14px 16px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 5px 14px rgba(15, 23, 42, 0.04);
    }

    /* 히어로 영역 */
    .hero-box {
        background: linear-gradient(135deg, #1f4f8f 0%, #3b82f6 100%);
        color: white;
        padding: 30px 34px;
        border-radius: 26px;
        margin-bottom: 24px;
        box-shadow: 0 10px 30px rgba(31, 79, 143, 0.22);
    }

    .hero-title {
        font-size: 2.1rem;
        font-weight: 900;
        margin-bottom: 8px;
        letter-spacing: -0.03em;
    }

    .hero-subtitle {
        font-size: 1rem;
        opacity: 0.94;
        line-height: 1.7;
        font-weight: 500;
    }

    /* 데이터프레임 여백 */
    div[data-testid="stDataFrame"] {
        border-radius: 14px;
        overflow: hidden;
        border: 1px solid #e2e8f0;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <div class="hero-box">
        <div class="hero-title">온라인 유대감 커뮤니티</div>
        <div class="hero-subtitle">
            관심사 기반 소그룹 배정, 게시물 공유, 댓글, 좋아요, 포인트, 신고 기능을 통해
            대규모 온라인 집단 안에서도 지속적인 소통이 이루어지도록 설계한 커뮤니티형 SNS 플랫폼입니다.
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

with st.sidebar:
    st.markdown("### 로그인")

    login_users_df = sheet_to_df(users_sheet, "users")

    if login_users_df.empty:
        st.info("아직 등록된 사용자가 없습니다.")
        st.caption("먼저 가입 탭에서 사용자를 등록하세요.")
    elif "password_hash" not in login_users_df.columns or "password_salt" not in login_users_df.columns:
        st.error("users 시트에 password_hash, password_salt 열을 추가해야 합니다.")
        st.caption("Google Sheets의 users 시트 맨 오른쪽에 password_hash, password_salt 열을 추가하세요.")
    else:
        login_options = []

        for _, user in login_users_df.iterrows():
            option_text = f'{user["nickname"]} / {user["user_id"]}'
            login_options.append(option_text)

        selected_login_option = st.selectbox(
            "사용자 선택",
            login_options,
            key="sidebar_login_user"
        )

        login_password = st.text_input(
            "비밀번호",
            type="password",
            key="sidebar_login_password"
        )

        if st.button("로그인", use_container_width=True):
            if login_password.strip() == "":
                st.warning("비밀번호를 입력하세요.")
            else:
                selected_user_id = selected_login_option.split(" / ")[-1]
                selected_user = login_users_df[login_users_df["user_id"] == selected_user_id].iloc[0]

                saved_password_hash = selected_user.get("password_hash", "")
                saved_password_salt = selected_user.get("password_salt", "")

                if str(saved_password_hash).strip() == "" or str(saved_password_salt).strip() == "":
                    st.warning("이 계정은 비밀번호가 설정되어 있지 않습니다. 새로 가입한 계정으로 테스트하세요.")
                elif verify_password(login_password, saved_password_hash, saved_password_salt):
                    st.session_state["login_user_id"] = selected_user["user_id"]
                    st.session_state["login_nickname"] = selected_user["nickname"]
                    st.session_state["login_group_id"] = selected_user["group_id"]

                    st.success(f'{selected_user["nickname"]}님으로 로그인되었습니다.')
                    st.rerun()
                else:
                    st.error("비밀번호가 일치하지 않습니다.")

        if is_logged_in():
            st.success(f'{get_login_nickname()}님 로그인 중')
            st.caption(f'소그룹 ID: {get_login_group_id()}')

            if st.button("로그아웃", use_container_width=True):
                st.session_state["login_user_id"] = ""
                st.session_state["login_nickname"] = ""
                st.session_state["login_group_id"] = ""
                st.rerun()

    st.divider()

    st.markdown("### 플랫폼 안내")
    st.write("관심사를 바탕으로 소그룹이 자동 배정됩니다.")
    st.write("공개 게시물은 전체 게시판에 표시됩니다.")
    st.write("내 그룹 게시판에서는 같은 소그룹의 게시물을 모아볼 수 있습니다.")
    st.write("댓글, 좋아요, 포인트, 신고 기능을 통해 커뮤니티 활동을 기록합니다.")

# =========================
# 본문 상단 로그인 영역
# 사이드바가 접혀서 안 보일 때를 대비한 로그인 박스
# =========================
with st.expander("로그인 / 로그아웃", expanded=True):
    login_users_df = sheet_to_df(users_sheet, "users")

    if login_users_df.empty:
        st.info("아직 등록된 사용자가 없습니다.")
        st.caption("먼저 가입 탭에서 사용자를 등록하세요.")
    elif "password_hash" not in login_users_df.columns or "password_salt" not in login_users_df.columns:
        st.error("users 시트에 password_hash, password_salt 열을 추가해야 합니다.")
        st.caption("Google Sheets의 users 시트 맨 오른쪽에 password_hash, password_salt 열을 추가하세요.")
    else:
        if is_logged_in():
            st.success(f'{get_login_nickname()}님 로그인 중')
            st.caption(f'소그룹 ID: {get_login_group_id()}')

            if st.button("로그아웃", key="main_logout_button"):
                st.session_state["login_user_id"] = ""
                st.session_state["login_nickname"] = ""
                st.session_state["login_group_id"] = ""
                st.rerun()
        else:
            login_options = []

            for _, user in login_users_df.iterrows():
                option_text = f'{user["nickname"]} / {user["user_id"]}'
                login_options.append(option_text)

            selected_login_option = st.selectbox(
                "사용자 선택",
                login_options,
                key="main_login_user"
            )

            login_password = st.text_input(
                "비밀번호",
                type="password",
                key="main_login_password"
            )

            if st.button("로그인", key="main_login_button"):
                if login_password.strip() == "":
                    st.warning("비밀번호를 입력하세요.")
                else:
                    selected_user_id = selected_login_option.split(" / ")[-1]
                    selected_user = login_users_df[login_users_df["user_id"] == selected_user_id].iloc[0]

                    saved_password_hash = selected_user.get("password_hash", "")
                    saved_password_salt = selected_user.get("password_salt", "")

                    if str(saved_password_hash).strip() == "" or str(saved_password_salt).strip() == "":
                        st.warning("이 계정은 비밀번호가 설정되어 있지 않습니다. 새로 가입한 계정으로 테스트하세요.")
                    elif verify_password(login_password, saved_password_hash, saved_password_salt):
                        st.session_state["login_user_id"] = selected_user["user_id"]
                        st.session_state["login_nickname"] = selected_user["nickname"]
                        st.session_state["login_group_id"] = selected_user["group_id"]

                        st.success(f'{selected_user["nickname"]}님으로 로그인되었습니다.')
                        st.rerun()
                    else:
                        st.error("비밀번호가 일치하지 않습니다.")
#tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs(["사용자 등록", "게시물 작성", "게시판 보기", "댓글 작성", "신고하기", "관리자 검토", "소그룹 보기", "내 소그룹 게시판", "활동 랭킹"])

tab1, tab2, tab3, tab7, tab8, tab9 = st.tabs([
    "가입",
    "글쓰기",
    "전체 게시판",
    #"댓글 작성",
    #"신고",
    #"관리자 검토",
    "소그룹",
    "내 그룹 게시판",
    "랭킹"
])
# =========================
# 1. 사용자 등록 탭
# =========================
with tab1:
    st.header("사용자 등록")

    with st.form("signup_form"):
        nickname = st.text_input("닉네임")

        password = st.text_input(
            "비밀번호",
            type="password",
            key="signup_password"
        )

        password_confirm = st.text_input(
            "비밀번호 확인",
            type="password",
            key="signup_password_confirm"
        )

        interests = st.multiselect(
            "관심사",
            ["공부", "게임", "음악", "운동", "영화", "독서", "진로", "일상", "기타"]
        )

        talk_style = st.selectbox(
            "대화 성향",
            ["조용한 편", "적극적인 편", "질문을 많이 하는 편", "공감 위주의 대화를 선호"]
        )

        purpose = st.selectbox(
            "대화 목적",
            ["친구 만들기", "고민 나누기", "정보 공유", "취미 공유", "학습/진로 이야기"]
        )

        submitted_signup = st.form_submit_button("사용자 저장")

    if submitted_signup:
        if nickname.strip() == "":
            st.warning("닉네임을 입력하세요.")
        elif password.strip() == "":
            st.warning("비밀번호를 입력하세요.")
        elif len(password) < 4:
            st.warning("비밀번호는 최소 4자 이상으로 설정하세요.")
        elif password != password_confirm:
            st.warning("비밀번호 확인이 일치하지 않습니다.")
        else:
            user_id = "user_" + datetime.now().strftime("%Y%m%d%H%M%S")
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if len(interests) == 0:
                main_interest = "기타"
            else:
                main_interest = interests[0]

            assigned_group_id, assigned_group_name = assign_group_to_user(
                groups_sheet,
                main_interest,
                user_id
            )

            password_hash, password_salt = make_password_hash(password)

            users_sheet.append_row([
                user_id,
                nickname,
                ", ".join(interests),
                talk_style,
                purpose,
                assigned_group_id,
                0,
                created_at,
                password_hash,
                password_salt
            ])

            clear_sheet_cache("users", "groups")

            st.success("사용자 정보가 저장되었습니다.")
            st.write("배정된 소그룹:", assigned_group_name)
            st.rerun()

    st.subheader("현재 등록된 사용자")
    users_df = sheet_to_df(users_sheet, "users")

    if users_df.empty:
        st.info("아직 등록된 사용자가 없습니다.")
    else:
        display_users_df = users_df.drop(
            columns=["password_hash", "password_salt"],
            errors="ignore"
        )

        st.dataframe(display_users_df)
# =========================
# 2. 글쓰기 탭
# =========================
with tab2:
    st.markdown("## 글쓰기")
    st.caption("로그인한 사용자 이름으로 게시물이 작성됩니다.")

    if not is_logged_in():
        st.warning("글을 작성하려면 먼저 로그인하세요.")
    else:
        selected_user_id = get_login_user_id()
        selected_nickname = get_login_nickname()

        st.info(f"현재 작성자: {selected_nickname}")

        with st.form("post_write_form"):
            title = st.text_input("게시물 제목")
            content = st.text_area("게시물 내용")

            post_type = st.selectbox(
                "게시물 유형",
                ["대화 요약", "질문", "후기", "의견", "기타"]
            )

            submitted_post = st.form_submit_button("게시물 저장")

        if submitted_post:
            if title.strip() == "":
                st.warning("제목을 입력하세요.")
            elif content.strip() == "":
                st.warning("내용을 입력하세요.")
            else:
                post_id = "post_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                risk_level = classify_risk(title + " " + content)
                status = decide_status(risk_level)
                likes = 0
                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                posts_sheet.append_row([
                    post_id,
                    selected_user_id,
                    title,
                    content,
                    post_type,
                    risk_level,
                    status,
                    likes,
                    created_at
                ])

                increase_user_points(users_sheet, selected_user_id, 10)
                clear_sheet_cache("posts", "users")

                st.success("게시물이 저장되었습니다.")
                st.write("위험도 분류:", risk_level)
                st.write("게시물 상태:", status)
                st.rerun()

# =========================
# 3. 전체 게시판 탭
# =========================
with tab3:
    st.markdown("## 전체 게시판")
    st.caption("공개 상태의 게시물만 표시됩니다.")

    posts_df = sheet_to_df(posts_sheet, "posts")
    users_df = sheet_to_df(users_sheet, "users")
    comments_df = sheet_to_df(comments_sheet, "comments")

    def get_nickname(user_id):
        if users_df.empty:
            return user_id

        matched_user = users_df[users_df["user_id"] == user_id]

        if matched_user.empty:
            return user_id
        else:
            return matched_user.iloc[0]["nickname"]

    if posts_df.empty:
        st.info("아직 작성된 게시물이 없습니다.")
    else:
        public_posts = posts_df[posts_df["status"] == "공개"]

        if public_posts.empty:
            st.info("공개된 게시물이 없습니다.")
        else:
            public_posts = public_posts.sort_values(by="created_at", ascending=False)

            for _, post in public_posts.iterrows():
                post_id = post["post_id"]
                writer_name = get_nickname(post["user_id"])

                if comments_df.empty:
                    post_comments = pd.DataFrame()
                    comment_count = 0
                else:
                    post_comments = comments_df[comments_df["post_id"] == post_id]
                    comment_count = len(post_comments)

                with st.container(border=True):
                    top_col1, top_col2 = st.columns([5, 1])

                    with top_col1:
                        st.markdown(f"### {post['title']}")
                        st.caption(f"{writer_name} · {post['post_type']} · {post['created_at']}")

                    with top_col2:
                        st.markdown(f"**좋아요 {post['likes']}**")
                        st.caption(f"댓글 {comment_count}개")

                    st.write(post["content"])

                    action_col1, action_col2 = st.columns([2, 6])

                    with action_col1:
                        if st.button("좋아요", key=f'board_like_{post_id}', use_container_width=True):
                            if not is_logged_in():
                                st.warning("좋아요를 누르려면 먼저 왼쪽 사이드바에서 로그인하세요.")
                            else:
                                success, message = add_like_once(
                                    likes_sheet,
                                    posts_sheet,
                                    users_sheet,
                                    post_id,
                                    post["user_id"],
                                    get_login_user_id()
                                )

                                if success:
                                    st.success(message)
                                    st.rerun()
                                else:
                                    st.warning(message)

                    with action_col2:
                        st.markdown('<span class="post-badge">공개 게시물</span>', unsafe_allow_html=True)

                    with st.expander("신고하기", expanded=False):
                        if not is_logged_in():
                            st.warning("신고하려면 먼저 왼쪽 사이드바에서 로그인하세요.")
                        else:
                            reporter_id = get_login_user_id()
                            reporter_nickname = get_login_nickname()

                            st.caption(f"신고자: {reporter_nickname}")

                            reason = st.selectbox(
                                "신고 사유",
                                [
                                    "부적절한 표현",
                                    "혐오 또는 비방",
                                    "위험한 내용",
                                    "개인정보 노출",
                                    "스팸 또는 도배",
                                    "기타"
                                ],
                                key=f"board_report_reason_{post_id}"
                            )

                            detail_reason = st.text_area(
                                "상세 신고 내용",
                                placeholder="필요하면 구체적인 신고 이유를 입력하세요.",
                                key=f"board_report_detail_{post_id}"
                            )

                            if st.button("신고 저장", key=f"board_save_report_{post_id}"):
                                report_id = "report_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                report_status = "미처리"

                                final_reason = reason

                                if detail_reason.strip() != "":
                                    final_reason = reason + " - " + detail_reason

                                reports_sheet.append_row([
                                    report_id,
                                    post_id,
                                    reporter_id,
                                    final_reason,
                                    report_status,
                                    created_at
                                ])

                                clear_sheet_cache("reports")

                                st.success("신고가 저장되었습니다.")
                                st.rerun()

                    st.markdown("---")

                    st.markdown("##### 댓글")

                    if comment_count == 0:
                        st.caption("아직 댓글이 없습니다.")
                    else:
                        post_comments = post_comments.sort_values(by="created_at", ascending=True)

                        for _, comment in post_comments.iterrows():
                            comment_writer = get_nickname(comment["user_id"])
                            st.markdown(f"**{comment_writer}**  \n{comment['content']}")
                            st.caption(comment["created_at"])

                    with st.expander("댓글 쓰기", expanded=False):
                        if not is_logged_in():
                            st.warning("댓글을 작성하려면 먼저 왼쪽 사이드바에서 로그인하세요.")
                        else:
                            selected_comment_user_id = get_login_user_id()
                            selected_comment_nickname = get_login_nickname()

                            st.caption(f"댓글 작성자: {selected_comment_nickname}")

                            new_comment_content = st.text_area(
                                "댓글 내용",
                                key=f'board_comment_content_{post_id}'
                            )

                            if st.button("댓글 저장", key=f'board_save_comment_{post_id}'):
                                if new_comment_content.strip() == "":
                                    st.warning("댓글 내용을 입력하세요.")
                                else:
                                    comment_id = "comment_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                    comments_sheet.append_row([
                                        comment_id,
                                        post_id,
                                        selected_comment_user_id,
                                        new_comment_content,
                                        created_at
                                    ])

                                    increase_user_points(users_sheet, selected_comment_user_id, 3)
                                    clear_sheet_cache("comments", "users")

                                    st.success("댓글이 저장되었습니다.")
                                    st.rerun()
# =========================
# 4. 댓글 작성 탭
# =========================
if False: #with tab4:
    st.header("댓글 작성")

    posts_df = sheet_to_df(posts_sheet, "posts")
    users_df = sheet_to_df(users_sheet, "users")

    if posts_df.empty:
        st.info("아직 작성된 게시물이 없습니다.")
    elif users_df.empty:
        st.warning("먼저 사용자 등록을 해야 댓글을 작성할 수 있습니다.")
    else:
        public_posts = posts_df[posts_df["status"] == "공개"]

        if public_posts.empty:
            st.info("댓글을 작성할 수 있는 공개 게시물이 없습니다.")
        else:
            post_options = []

            for _, post in public_posts.iterrows():
                option_text = f'{post["title"]} / {post["post_id"]}'
                post_options.append(option_text)

            selected_post_option = st.selectbox("댓글을 달 게시물 선택", post_options)

            selected_post_id = selected_post_option.split(" / ")[-1]
            selected_post = public_posts[public_posts["post_id"] == selected_post_id].iloc[0]

            st.subheader("선택한 게시물")
            st.write("제목:", selected_post["title"])
            st.write("내용:", selected_post["content"])

            user_options = users_df["nickname"].tolist()
            selected_nickname = st.selectbox("댓글 작성자 선택", user_options)

            selected_user = users_df[users_df["nickname"] == selected_nickname].iloc[0]
            selected_user_id = selected_user["user_id"]

            comment_content = st.text_area("댓글 내용")

            if st.button("댓글 저장"):
                if comment_content.strip() == "":
                    st.warning("댓글 내용을 입력하세요.")
                else:
                    comment_id = "comment_" + datetime.now().strftime("%Y%m%d%H%M%S")
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    comments_sheet.append_row([
                        comment_id,
                        selected_post_id,
                        selected_user_id,
                        comment_content,
                        created_at
                    ])

                    increase_user_points(users_sheet, selected_user_id, 3)
                    clear_sheet_cache("comments", "users")

                    st.success("댓글이 저장되었습니다.")
# =========================
# 5. 신고하기 탭
# =========================
if False: #with tab5
    st.header("게시물 신고하기")

    posts_df = sheet_to_df(posts_sheet, "posts")
    users_df = sheet_to_df(users_sheet, "users")

    if posts_df.empty:
        st.info("아직 신고할 수 있는 게시물이 없습니다.")
    elif users_df.empty:
        st.warning("먼저 사용자 등록을 해야 신고할 수 있습니다.")
    else:
        public_posts = posts_df[posts_df["status"] == "공개"]

        if public_posts.empty:
            st.info("신고할 수 있는 공개 게시물이 없습니다.")
        else:
            post_options = []

            for _, post in public_posts.iterrows():
                option_text = f'{post["title"]} / {post["post_id"]}'
                post_options.append(option_text)

            selected_post_option = st.selectbox("신고할 게시물 선택", post_options)

            selected_post_id = selected_post_option.split(" / ")[-1]
            selected_post = public_posts[public_posts["post_id"] == selected_post_id].iloc[0]

            st.subheader("선택한 게시물")
            st.write("제목:", selected_post["title"])
            st.write("내용:", selected_post["content"])

            user_options = users_df["nickname"].tolist()
            selected_nickname = st.selectbox("신고자 선택", user_options)

            selected_user = users_df[users_df["nickname"] == selected_nickname].iloc[0]
            reporter_id = selected_user["user_id"]

            reason = st.selectbox(
                "신고 사유",
                [
                    "부적절한 표현",
                    "혐오 또는 비방",
                    "위험한 내용",
                    "개인정보 노출",
                    "스팸 또는 도배",
                    "기타"
                ]
            )

            detail_reason = st.text_area("상세 신고 내용", placeholder="필요하면 구체적인 신고 이유를 입력하세요.")

            if st.button("신고 저장"):
                report_id = "report_" + datetime.now().strftime("%Y%m%d%H%M%S")
                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                report_status = "미처리"

                final_reason = reason

                if detail_reason.strip() != "":
                    final_reason = reason + " - " + detail_reason

                reports_sheet.append_row([
                    report_id,
                    selected_post_id,
                    reporter_id,
                    final_reason,
                    report_status,
                    created_at
                ])

                clear_sheet_cache("reports")

                st.success("신고가 저장되었습니다.")
# =========================
# 6. 관리자 검토 탭
# =========================
if False: #with tab6:
    st.header("관리자 검토 페이지")

    admin_password = st.text_input("관리자 비밀번호", type="password")

    if admin_password != "1234":
        st.warning("관리자 비밀번호를 입력해야 검토 페이지를 볼 수 있습니다.")
    else:
        st.success("관리자 인증 완료")

        posts_df = sheet_to_df(posts_sheet, "posts")
        users_df = sheet_to_df(users_sheet, "users")
        reports_df = sheet_to_df(reports_sheet, "reports")

        def get_nickname(user_id):
            if users_df.empty:
                return user_id

            matched_user = users_df[users_df["user_id"] == user_id]

            if matched_user.empty:
                return user_id
            else:
                return matched_user.iloc[0]["nickname"]

        st.subheader("1. 검토대기 게시물")

        if posts_df.empty:
            st.info("게시물이 없습니다.")
        else:
            review_posts = posts_df[posts_df["status"] == "검토대기"]

            if review_posts.empty:
                st.info("검토대기 게시물이 없습니다.")
            else:
                for _, post in review_posts.iterrows():
                    writer_name = get_nickname(post["user_id"])

                    with st.container():
                        st.write("게시물 ID:", post["post_id"])
                        st.write("작성자:", writer_name)
                        st.write("제목:", post["title"])
                        st.write("내용:", post["content"])
                        st.write("게시물 유형:", post["post_type"])
                        st.write("위험도:", post["risk_level"])
                        st.write("현재 상태:", post["status"])

                        col1, col2 = st.columns(2)

                        with col1:
                            if st.button("공개 승인", key=f'approve_{post["post_id"]}'):
                                update_post_status(posts_sheet, post["post_id"], "공개")
                                st.success("게시물이 공개 처리되었습니다.")
                                st.rerun()

                        with col2:
                            if st.button("차단 처리", key=f'block_{post["post_id"]}'):
                                update_post_status(posts_sheet, post["post_id"], "차단")
                                st.success("게시물이 차단 처리되었습니다.")
                                st.rerun()

                        st.divider()

        st.subheader("2. 미처리 신고 목록")

        if reports_df.empty:
            st.info("신고 내역이 없습니다.")
        else:
            pending_reports = reports_df[reports_df["status"] == "미처리"]

            if pending_reports.empty:
                st.info("미처리 신고가 없습니다.")
            else:
                for _, report in pending_reports.iterrows():
                    reported_post_id = report["post_id"]
                    matched_post = posts_df[posts_df["post_id"] == reported_post_id]

                    with st.container():
                        st.write("신고 ID:", report["report_id"])
                        st.write("신고자:", get_nickname(report["reporter_id"]))
                        st.write("신고 사유:", report["reason"])
                        st.write("신고 상태:", report["status"])
                        st.write("신고 시간:", report["created_at"])

                        if matched_post.empty:
                            st.warning("해당 게시물을 찾을 수 없습니다.")
                        else:
                            post = matched_post.iloc[0]

                            st.markdown("#### 신고된 게시물")
                            st.write("게시물 ID:", post["post_id"])
                            st.write("작성자:", get_nickname(post["user_id"]))
                            st.write("제목:", post["title"])
                            st.write("내용:", post["content"])
                            st.write("현재 게시물 상태:", post["status"])

                            col1, col2 = st.columns(2)

                            with col1:
                                if st.button("신고 처리 완료", key=f'report_done_{report["report_id"]}'):
                                    update_report_status(reports_sheet, report["report_id"], "처리완료")
                                    st.success("신고가 처리완료 상태로 변경되었습니다.")
                                    st.rerun()

                            with col2:
                                if st.button("게시물 차단 후 신고 처리", key=f'report_block_{report["report_id"]}'):
                                    update_post_status(posts_sheet, post["post_id"], "차단")
                                    update_report_status(reports_sheet, report["report_id"], "처리완료")
                                    st.success("게시물을 차단하고 신고를 처리완료로 변경했습니다.")
                                    st.rerun()

                        st.divider()
# =========================
# 7. 소그룹 보기 탭
# =========================
with tab7:
    st.header("소그룹 보기")

    groups_df = sheet_to_df(groups_sheet, "groups")
    users_df = sheet_to_df(users_sheet, "users")

    def get_nickname_for_group(user_id):
        if users_df.empty:
            return user_id

        matched_user = users_df[users_df["user_id"] == user_id]

        if matched_user.empty:
            return user_id
        else:
            return matched_user.iloc[0]["nickname"]

    if groups_df.empty:
        st.info("아직 생성된 소그룹이 없습니다.")
    else:
        for _, group in groups_df.iterrows():
            st.subheader(group["group_name"])
            st.write("소그룹 ID:", group["group_id"])
            st.write("대표 관심사:", group["main_interest"])

            member_ids_text = str(group["member_ids"])

            if member_ids_text.strip() == "":
                st.write("구성원: 없음")
            else:
                member_ids = [member.strip() for member in member_ids_text.split(",") if member.strip() != ""]
                member_names = [get_nickname_for_group(member_id) for member_id in member_ids]

                st.write("구성원:", ", ".join(member_names))
                st.write("현재 인원:", len(member_names), "명")

            st.divider()
# =========================
# 8. 내 그룹 게시판 탭
# =========================
with tab8:
    st.markdown("## 내 그룹 게시판")
    st.caption("로그인한 사용자와 같은 소그룹에 속한 구성원의 공개 게시물만 표시됩니다.")

    users_df = sheet_to_df(users_sheet, "users")
    posts_df = sheet_to_df(posts_sheet, "posts")
    comments_df = sheet_to_df(comments_sheet, "comments")

    def get_nickname_for_group_board(user_id):
        if users_df.empty:
            return user_id

        matched_user = users_df[users_df["user_id"] == user_id]

        if matched_user.empty:
            return user_id
        else:
            return matched_user.iloc[0]["nickname"]

    if not is_logged_in():
        st.warning("내 그룹 게시판을 보려면 먼저 왼쪽 사이드바에서 로그인하세요.")
    elif users_df.empty:
        st.warning("먼저 사용자 등록을 해야 소그룹 게시판을 볼 수 있습니다.")
    elif posts_df.empty:
        st.info("아직 작성된 게시물이 없습니다.")
    else:
        login_user_id = get_login_user_id()
        login_nickname = get_login_nickname()
        login_group_id = get_login_group_id()

        if login_group_id == "" or pd.isna(login_group_id):
            st.warning("현재 로그인한 사용자는 아직 소그룹에 배정되지 않았습니다.")
        else:
            st.info(f"{login_nickname}님의 소그룹 게시판입니다.")

            group_members = users_df[users_df["group_id"] == login_group_id]
            group_member_ids = group_members["user_id"].tolist()
            group_member_names = group_members["nickname"].tolist()

            info_col1, info_col2 = st.columns([2, 5])

            with info_col1:
                st.metric("소그룹 인원", len(group_member_names))

            with info_col2:
                st.write("같은 소그룹 구성원")
                st.caption(", ".join(group_member_names))

            public_posts = posts_df[posts_df["status"] == "공개"]
            group_posts = public_posts[public_posts["user_id"].isin(group_member_ids)]

            st.markdown("### 소그룹 게시물")

            if group_posts.empty:
                st.info("아직 이 소그룹에 공개 게시물이 없습니다.")
            else:
                group_posts = group_posts.sort_values(by="created_at", ascending=False)

                for _, post in group_posts.iterrows():
                    post_id = post["post_id"]
                    writer_name = get_nickname_for_group_board(post["user_id"])

                    if comments_df.empty:
                        post_comments = pd.DataFrame()
                        comment_count = 0
                    else:
                        post_comments = comments_df[comments_df["post_id"] == post_id]
                        comment_count = len(post_comments)

                    with st.container(border=True):
                        top_col1, top_col2 = st.columns([5, 1])

                        with top_col1:
                            st.markdown(f"### {post['title']}")
                            st.caption(f"{writer_name} · {post['post_type']} · {post['created_at']}")

                        with top_col2:
                            st.markdown(f"**좋아요 {post['likes']}**")
                            st.caption(f"댓글 {comment_count}개")

                        st.write(post["content"])

                        action_col1, action_col2 = st.columns([2, 6])

                        with action_col1:
                            if st.button("좋아요", key=f'group_like_{post_id}', use_container_width=True):
                                if not is_logged_in():
                                    st.warning("좋아요를 누르려면 먼저 왼쪽 사이드바에서 로그인하세요.")
                                else:
                                    success, message = add_like_once(
                                        likes_sheet,
                                        posts_sheet,
                                        users_sheet,
                                        post_id,
                                        post["user_id"],
                                        get_login_user_id()
                                    )

                                    if success:
                                        st.success(message)
                                        st.rerun()
                                    else:
                                        st.warning(message)

                        with action_col2:
                            st.markdown('<span class="post-badge">소그룹 게시물</span>', unsafe_allow_html=True)

                        with st.expander("신고하기", expanded=False):
                            if not is_logged_in():
                                st.warning("신고하려면 먼저 왼쪽 사이드바에서 로그인하세요.")
                            else:
                                reporter_id = get_login_user_id()
                                reporter_nickname = get_login_nickname()

                                st.caption(f"신고자: {reporter_nickname}")

                                reason = st.selectbox(
                                    "신고 사유",
                                    [
                                        "부적절한 표현",
                                        "혐오 또는 비방",
                                        "위험한 내용",
                                        "개인정보 노출",
                                        "스팸 또는 도배",
                                        "기타"
                                    ],
                                    key=f"group_report_reason_{post_id}"
                                )

                                detail_reason = st.text_area(
                                    "상세 신고 내용",
                                    placeholder="필요하면 구체적인 신고 이유를 입력하세요.",
                                    key=f"group_report_detail_{post_id}"
                                )

                                if st.button("신고 저장", key=f"group_save_report_{post_id}"):
                                    report_id = "report_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    report_status = "미처리"

                                    final_reason = reason

                                    if detail_reason.strip() != "":
                                        final_reason = reason + " - " + detail_reason

                                    reports_sheet.append_row([
                                        report_id,
                                        post_id,
                                        reporter_id,
                                        final_reason,
                                        report_status,
                                        created_at
                                    ])

                                    clear_sheet_cache("reports")

                                    st.success("신고가 저장되었습니다.")
                                    st.rerun()

                        st.markdown("---")

                        st.markdown("##### 댓글")

                        if comment_count == 0:
                            st.caption("아직 댓글이 없습니다.")
                        else:
                            post_comments = post_comments.sort_values(by="created_at", ascending=True)

                            for _, comment in post_comments.iterrows():
                                comment_writer = get_nickname_for_group_board(comment["user_id"])
                                st.markdown(
                                    f"""
                                    <div class="comment-box">
                                        <strong>{comment_writer}</strong><br>
                                        {comment['content']}<br>
                                        <span class="post-meta">{comment['created_at']}</span>
                                    </div>
                                    """,
                                    unsafe_allow_html=True
                                )

                        with st.expander("댓글 쓰기", expanded=False):
                            if not is_logged_in():
                                st.warning("댓글을 작성하려면 먼저 왼쪽 사이드바에서 로그인하세요.")
                            elif get_login_group_id() != login_group_id:
                                st.warning("같은 소그룹 구성원만 댓글을 작성할 수 있습니다.")
                            else:
                                selected_group_comment_user_id = get_login_user_id()
                                selected_group_comment_nickname = get_login_nickname()

                                st.caption(f"댓글 작성자: {selected_group_comment_nickname}")

                                new_group_comment_content = st.text_area(
                                    "댓글 내용",
                                    key=f'group_comment_content_{post_id}'
                                )

                                if st.button("댓글 저장", key=f'group_save_comment_{post_id}'):
                                    if new_group_comment_content.strip() == "":
                                        st.warning("댓글 내용을 입력하세요.")
                                    else:
                                        comment_id = "comment_" + datetime.now().strftime("%Y%m%d%H%M%S%f")
                                        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                        comments_sheet.append_row([
                                            comment_id,
                                            post_id,
                                            selected_group_comment_user_id,
                                            new_group_comment_content,
                                            created_at
                                        ])

                                        increase_user_points(users_sheet, selected_group_comment_user_id, 3)
                                        clear_sheet_cache("comments", "users")

                                        st.success("댓글이 저장되었습니다.")
                                        st.rerun()
# =========================
# 9. 활동 랭킹 탭
# =========================
with tab9:
    st.header("활동 랭킹")

    users_df = sheet_to_df(users_sheet, "users")
    posts_df = sheet_to_df(posts_sheet, "posts")
    comments_df = sheet_to_df(comments_sheet, "comments")

    if users_df.empty:
        st.info("아직 등록된 사용자가 없습니다.")
    else:
        users_df["points"] = pd.to_numeric(users_df["points"], errors="coerce").fillna(0).astype(int)
        ranking_df = users_df.sort_values(by="points", ascending=False)

        st.subheader("포인트 순위")

        for rank, (_, user) in enumerate(ranking_df.iterrows(), start=1):
            st.write(f'{rank}위 | {user["nickname"]} | {user["points"]}점')

        st.divider()

        st.subheader("사용자별 활동 현황")

        for _, user in ranking_df.iterrows():
            user_id = user["user_id"]

            if posts_df.empty:
                post_count = 0
            else:
                post_count = len(posts_df[posts_df["user_id"] == user_id])

            if comments_df.empty:
                comment_count = 0
            else:
                comment_count = len(comments_df[comments_df["user_id"] == user_id])

            st.write(
                f'{user["nickname"]}: 게시물 {post_count}개, 댓글 {comment_count}개, 포인트 {user["points"]}점'
            )