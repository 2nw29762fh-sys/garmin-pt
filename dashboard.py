import os
import json
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from google import genai
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# --- 1. INSTÄLLNINGAR & MILJÖ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

st.set_page_config(page_title="Jägar-PT Analytics", page_icon="🪖", layout="wide", initial_sidebar_state="collapsed")

# --- 2. CUSTOM CSS (Dark Mode & Kortdesign) ---
st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
    header {visibility: hidden;} footer {visibility: hidden;}
    
    .metric-card { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; }
    .metric-label { font-size: 11px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-value { font-size: 26px; font-weight: 700; color: #f0f6fc; margin: 4px 0; }
    .metric-sub { font-size: 11px; color: #8b949e; }
    .metric-sub-green { color: #3fb950; font-weight: 600; }
    .metric-sub-red { color: #f85149; font-weight: 600; }

    .callout-box { background-color: #161b22; border: 1px solid #30363d; border-left: 4px solid #d29922; border-radius: 6px; padding: 12px 16px; margin-bottom: 12px; }
    .callout-title { font-size: 13px; font-weight: 700; color: #f0f6fc; margin-bottom: 4px; }
    .callout-text { font-size: 12px; color: #8b949e; line-height: 1.4; }

    .day-card { background-color: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px; text-align: center; min-height: 110px; }
    .day-header { font-size: 10px; font-weight: 700; color: #8b949e; }
    .day-title { font-size: 13px; font-weight: 700; color: #f0f6fc; margin: 4px 0; }
    .day-detail { font-size: 10px; color: #8b949e; }
    .badge-rest { background-color: #21262d; color: #8b949e; border-radius: 4px; padding: 2px 6px; font-size: 10px; }
    .badge-easy { background-color: #1f6feb22; color: #58a6ff; border-radius: 4px; padding: 2px 6px; font-size: 10px; }
    .badge-hard { background-color: #da363322; color: #f85149; border-radius: 4px; padding: 2px 6px; font-size: 10px; }

    .stTabs [data-baseweb="tab-list"] { gap: 12px; border-bottom: 1px solid #30363d; }
    .stTabs [data-baseweb="tab"] { color: #8b949e; font-size: 13px; font-weight: 600; padding: 8px 16px; }
    .stTabs [aria-selected="true"] { color: #58a6ff !important; border-bottom: 2px solid #58a6ff !important; }
</style>
""", unsafe_allow_html=True)

# --- 3. FIREBASE INIT ---
if not firebase_admin._apps:
    fb_creds = os.environ.get("FIREBASE_CREDENTIALS")
    if fb_creds:
        cred_dict = json.loads(fb_creds)
        cred = credentials.Certificate(cred_dict)
    else:
        KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")
        if os.path.exists(KEY_PATH):
            cred = credentials.Certificate(KEY_PATH)
        else:
            st.error("Inloggningsuppgifter till Firebase saknas.")
            st.stop()
    firebase_admin.initialize_app(cred)

db = firestore.client()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 4. DATA HÄMTNING ---
@st.cache_data(ttl=30)
def load_data():
    docs = db.collection("garmin_history").stream()
    data = [doc.to_dict() for doc in docs]
    df = pd.DataFrame(data)
    if not df.empty and 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        # Konvertera relevanta kolumner till siffror
        numeric_cols = ['readiness', 'sleep_score', 'vo2max', 'löpvolym_km', 'resting_hr', 'hrv_avg', 
                        'ctl_fitness', 'atl_fatigue', 'deep_sleep_min', 'rem_sleep_min', 'stress_avg', 'steps']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        # Beräkna Form (TSB)
        if 'ctl_fitness' in df.columns and 'atl_fatigue' in df.columns:
            df['tsb_form'] = df['ctl_fitness'] - df['atl_fatigue']
        else:
            df['tsb_form'] = 0
    return df

df = load_data()

if df.empty:
    st.info("Ingen data hittades i Firebase.")
    st.stop()

latest = df.iloc[-1]

# --- 5. TOPP-HEADER & KPIS ---
st.markdown("""
<div style="margin-bottom: 15px;">
    <div style="font-size: 22px; font-weight: 800; color: #f0f6fc;">JÄGARTJÄNST / MÖNSTRING PR-PREPARATION</div>
    <div style="font-size: 12px; color: #8b949e;">
        <b>Status:</b> Aktiv &nbsp;|&nbsp; <b>Garmin Prognos:</b> Cykeltest: Max &nbsp;•&nbsp; ISOK: Max (Nivå 9)
    </div>
</div>
""", unsafe_allow_html=True)

col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)

fitness_val = latest.get('ctl_fitness', 65.3)
fatigue_val = latest.get('atl_fatigue', 91.7)
form_val = latest.get('tsb_form', -26.4)
vol_val = df['löpvolym_km'].tail(7).sum() if 'löpvolym_km' in df.columns else 0.0
vo2_val = latest.get('vo2max', 56.0)
rhr_val = latest.get('resting_hr', 47)
hrv_val = latest.get('hrv_avg', 91)
sleep_val = latest.get('sleep_score', 79)

with col1:
    st.markdown(f'<div class="metric-card"><div class="metric-label">Fitness</div><div class="metric-value">{fitness_val:.1f}</div><div class="metric-sub metric-sub-green">▲ 7-day trend</div></div>', unsafe_allow_html=True)
with col2:
    st.markdown(f'<div class="metric-card"><div class="metric-label">Fatigue</div><div class="metric-value">{fatigue_val:.1f}</div><div class="metric-sub metric-sub-red">▲ 7-day load</div></div>', unsafe_allow_html=True)
with col3:
    st.markdown(f'<div class="metric-card"><div class="metric-label">Form</div><div class="metric-value">{form_val:.1f}</div><div class="metric-sub {"metric-sub-red" if form_val < -10 else "metric-sub-green"}">4-day avg</div></div>', unsafe_allow_html=True)
with col4:
    st.markdown(f'<div class="metric-card"><div class="metric-label">Weekly Vol</div><div class="metric-value">{vol_val:.1f} <span style="font-size:14px;">km</span></div><div class="metric-sub">7-day total</div></div>', unsafe_allow_html=True)
with col5:
    st.markdown(f'<div class="metric-card"><div class="metric-label">VO2 Max</div><div class="metric-value">{vo2_val}</div><div class="metric-sub metric-sub-green">Top 1%</div></div>', unsafe_allow_html=True)
with col6:
    st.markdown(f'<div class="metric-card"><div class="metric-label">Resting HR</div><div class="metric-value">{rhr_val} <span style="font-size:14px;">bpm</span></div><div class="metric-sub">Latest reading</div></div>', unsafe_allow_html=True)
with col7:
    st.markdown(f'<div class="metric-card"><div class="metric-label">HRV Avg</div><div class="metric-value">{hrv_val} <span style="font-size:14px;">ms</span></div><div class="metric-sub metric-sub-green">Balanced</div></div>', unsafe_allow_html=True)
with col8:
    st.markdown(f'<div class="metric-card"><div class="metric-label">Sleep Score</div><div class="metric-value">{sleep_val}</div><div class="metric-sub">Last night</div></div>', unsafe_allow_html=True)

# --- 6. FLIKAR ---
tab_plan, tab_fitness, tab_effort, tab_recovery, tab_runs, tab_chat = st.tabs([
    "Plan", "Fitness", "Effort", "Recovery", "Runs", "💬 PT-Chatt"
])

# -- PLAN --
with tab_plan:
    st.markdown("**NEXT 10 DAYS SCHEDULE**")
    days_cols = st.columns(7)
    plan_days = [
        {"day": "MÅN", "title": "ISOK-Styrka", "detail": "Marklyft 5x5 + Benpress", "type": "hard"},
        {"day": "TIS", "title": "VO2-Max", "detail": "4x4 min Intervaller", "type": "hard"},
        {"day": "ONS", "title": "Återhämtning", "detail": "Rörlighet + Promenad", "type": "rest"},
        {"day": "TOR", "title": "Jägartest", "detail": "15 min Step-up + Chins", "type": "hard"},
        {"day": "FRE", "title": "Uthållighet", "detail": "BJJ eller Marsch", "type": "easy"},
        {"day": "LÖR", "title": "Styrka/Power", "detail": "Frivändningar + Bål", "type": "easy"},
        {"day": "SÖN", "title": "Vila", "detail": "Sömn & Kost", "type": "rest"}
    ]
    for idx, d in enumerate(plan_days):
        with days_cols[idx]:
            st.markdown(f'<div class="day-card"><div class="day-header">{d["day"]}</div><div class="day-title">{d["title"]}</div><div class="day-detail">{d["detail"]}</div><br><span class="badge-{d["type"]}">{d["type"].upper()}</span></div>', unsafe_allow_html=True)

# -- FITNESS --
with tab_fitness:
    st.markdown("**FITNESS & FATIGUE OVER TIME (CTL / ATL)**")
    fig_fit = go.Figure()
    fig_fit.add_trace(go.Scatter(x=df['date'], y=df['ctl_fitness'], name='Fitness (CTL)', line=dict(color='#58a6ff', width=3)))
    fig_fit.add_trace(go.Scatter(x=df['date'], y=df['atl_fatigue'], name='Fatigue (ATL)', line=dict(color='#f85149', width=2)))
    fig_fit.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=280, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h", y=1.02, x=1))
    st.plotly_chart(fig_fit, use_container_width=True)

    col_act1, col_act2, col_act3 = st.columns(3)
    with col_act1:
        st.markdown(f'<div class="metric-card"><div class="metric-label">Daily Steps</div><div class="metric-value">{latest.get("steps", 0):,}</div><div class="metric-sub">Foot conditioning (NEAT)</div></div>', unsafe_allow_html=True)
    with col_act2:
        st.markdown(f'<div class="metric-card"><div class="metric-label">Form (TSB)</div><div class="metric-value">{form_val:.1f}</div><div class="metric-sub">Current freshness</div></div>', unsafe_allow_html=True)
    with col_act3:
        st.markdown(f'<div class="metric-card"><div class="metric-label">Training Days</div><div class="metric-value">{df["workout_count"].tail(7).sum():.0f}</div><div class="metric-sub">Last 7 days</div></div>', unsafe_allow_html=True)

    if 'steps' in df.columns:
        fig_steps = px.bar(df, x='date', y='steps', title="Daily Steps Volume")
        fig_steps.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=220, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_steps, use_container_width=True)

# -- EFFORT (Den tidigare tomma fliken) --
with tab_effort:
    st.markdown("**TRAINING EFFORT & INTENSITY**")
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        st.markdown("**Readiness vs Training Load (Correlation)**")
        # Scatterplot för att se relationen mellan readiness och volym/belastning
        if 'readiness' in df.columns and 'löpvolym_km' in df.columns:
            fig_eff = px.scatter(df, x='readiness', y='löpvolym_km', size='workout_count', color='tsb_form',
                                 color_continuous_scale='RdYlGn', labels={'readiness': 'Readiness Score', 'löpvolym_km': 'Volume (km)', 'tsb_form': 'Form'})
            fig_eff.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=300, margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig_eff, use_container_width=True)
    with col_e2:
        st.markdown("**Heart Rate Zones & Activity Intensity**")
        st.info("Här kommer vi kunna plotta din exakta pulszonsfördelning (Zon 1-5) när detaljerad .FIT-data från dina BJJ- och löppass matas in kontinuerligt. Just nu analyserar AI:n din Effort via Akut/Kronisk Load Ratio i bakgrunden.")

# -- RECOVERY (Sömnarkitektur och Stress) --
with tab_recovery:
    st.markdown("**HEART RATE VARIABILITY & RECOVERY TRENDS**")
    fig_rec = px.line(df, x='date', y=[c for c in ['readiness', 'sleep_score'] if c in df.columns],
                      color_discrete_sequence=['#3fb950', '#58a6ff'], markers=True)
    fig_rec.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=250, margin=dict(l=0,r=0,t=10,b=0))
    st.plotly_chart(fig_rec, use_container_width=True)

    st.markdown("<br><b>BIOMETRICS (Stress, Body Battery & Sleep Architecture)</b>", unsafe_allow_html=True)
    col_r1, col_r2, col_r3, col_r4 = st.columns(4)
    with col_r1:
        st.markdown(f'<div class="metric-card"><div class="metric-label">Avg Stress</div><div class="metric-value">{latest.get("stress_avg", "N/A")}</div><div class="metric-sub">0-100 scale</div></div>', unsafe_allow_html=True)
    with col_r2:
        st.markdown(f'<div class="metric-card"><div class="metric-label">Battery High</div><div class="metric-value">{latest.get("bb_max", "N/A")}%</div><div class="metric-sub">Morning charge</div></div>', unsafe_allow_html=True)
    with col_r3:
        st.markdown(f'<div class="metric-card"><div class="metric-label">Deep Sleep</div><div class="metric-value">{latest.get("deep_sleep_min", 0)} <span style="font-size:14px;">min</span></div><div class="metric-sub">Physical repair</div></div>', unsafe_allow_html=True)
    with col_r4:
        st.markdown(f'<div class="metric-card"><div class="metric-label">REM Sleep</div><div class="metric-value">{latest.get("rem_sleep_min", 0)} <span style="font-size:14px;">min</span></div><div class="metric-sub">Mental repair</div></div>', unsafe_allow_html=True)

    col_sg1, col_sg2 = st.columns(2)
    with col_sg1:
        st.markdown("**Sleep Architecture (Deep vs REM)**")
        if 'deep_sleep_min' in df.columns and 'rem_sleep_min' in df.columns:
            fig_sleep = go.Figure()
            fig_sleep.add_trace(go.Bar(x=df['date'], y=df['deep_sleep_min'], name='Deep Sleep (Fysisk)', marker_color='#1f6feb'))
            fig_sleep.add_trace(go.Bar(x=df['date'], y=df['rem_sleep_min'], name='REM Sleep (Mental)', marker_color='#8957e5'))
            fig_sleep.update_layout(barmode='stack', template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=250, margin=dict(l=0,r=0,t=10,b=0), legend=dict(orientation="h", y=1.02, x=1))
            st.plotly_chart(fig_sleep, use_container_width=True)

    with col_sg2:
        st.markdown("**Daily Stress Level**")
        if 'stress_avg' in df.columns:
            df_stress = df[df['stress_avg'].apply(lambda x: str(x).isnumeric() if pd.notnull(x) else False)]
            if not df_stress.empty:
                fig_stress = px.area(df_stress, x='date', y='stress_avg', color_discrete_sequence=['#d29922'])
                fig_stress.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=250, margin=dict(l=0,r=0,t=10,b=0))
                st.plotly_chart(fig_stress, use_container_width=True)

# -- RUNS --
with tab_runs:
    st.markdown("**RAW DATA LOG**")
    st.dataframe(df, use_container_width=True)

# -- CHAT --
with tab_chat:
    st.markdown("**MILITARY PT — INTERACTIVE ADVISORY**")
    if not GEMINI_API_KEY:
        st.error("Gemini API-nyckel saknas.")
    else:
        if "messages" not in st.session_state:
            st.session_state.messages = []
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("Skriv till din coach..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            context_data = df.tail(7).to_json(orient='records')
            full_prompt = f"""Du är en stenhård men pedagogisk militär PT för Mönstring och Jägartestet.
Här är användarens senaste mätvärden (inkl HRV, Readiness, Sömnarkitektur): {context_data}
Svara kortfattat, auktoritärt och konstruktivt på svenska.
Fråga: {prompt}"""

            with st.chat_message("assistant"):
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    response = client.models.generate_content(model="gemini-3.5-flash", contents=full_prompt)
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Kunde inte hämta svar från Gemini: {e}")
