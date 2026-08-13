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
from datetime import datetime, date

# --- 1. INSTÄLLNINGAR & SIDA ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

st.set_page_config(
    page_title="Garmin & AI Performance Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- 2. EXAKT MATCHNING AV VIDEONS MÖRKA DESIGN (CUSTOM CSS) ---
st.markdown("""
<style>
    /* Huvudbakgrund - Mörkt grafitgrå */
    .stApp {
        background-color: #0e1117;
        color: #c9d1d9;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* Dölj Streamlit-header & footer */
    header {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Kort-design (Kopierat från videon) */
    .metric-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
    }
    .metric-label {
        font-size: 11px;
        font-weight: 600;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-value {
        font-size: 26px;
        font-weight: 700;
        color: #f0f6fc;
        margin: 4px 0;
    }
    .metric-sub {
        font-size: 11px;
        color: #8b949e;
    }
    .metric-sub-green { color: #3fb950; font-weight: 600; }
    .metric-sub-red { color: #f85149; font-weight: 600; }

    /* Callout rutor (Back off / Advice) */
    .callout-box {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-left: 4px solid #d29922;
        border-radius: 6px;
        padding: 12px 16px;
        margin-bottom: 12px;
    }
    .callout-title {
        font-size: 13px;
        font-weight: 700;
        color: #f0f6fc;
        margin-bottom: 4px;
    }
    .callout-text {
        font-size: 12px;
        color: #8b949e;
        line-height: 1.4;
    }

    /* Dagskort för 10-dagarsplanen */
    .day-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 10px;
        text-align: center;
        min-height: 110px;
    }
    .day-header { font-size: 10px; font-weight: 700; color: #8b949e; }
    .day-title { font-size: 13px; font-weight: 700; color: #f0f6fc; margin: 4px 0; }
    .day-detail { font-size: 10px; color: #8b949e; }
    .badge-rest { background-color: #21262d; color: #8b949e; border-radius: 4px; padding: 2px 6px; font-size: 10px; }
    .badge-easy { background-color: #1f6feb22; color: #58a6ff; border-radius: 4px; padding: 2px 6px; font-size: 10px; }
    .badge-hard { background-color: #da363322; color: #f85149; border-radius: 4px; padding: 2px 6px; font-size: 10px; }

    /* Flikdesign */
    .stTabs [data-baseweb="tab-list"] { gap: 12px; border-bottom: 1px solid #30363d; }
    .stTabs [data-baseweb="tab"] { color: #8b949e; font-size: 13px; font-weight: 600; padding: 8px 16px; }
    .stTabs [aria-selected="true"] { color: #58a6ff !important; border-bottom: 2px solid #58a6ff !important; }
</style>
""", unsafe_allow_html=True)

# --- 3. INITIERA FIREBASE ---
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
            st.error("Hittade inga inloggningsuppgifter till Firebase.")
            st.stop()
    firebase_admin.initialize_app(cred)

db = firestore.client()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 4. LADDA OCH BEARBETA DATA ---
@st.cache_data(ttl=30)
def load_data():
    docs = db.collection("garmin_history").stream()
    data = [doc.to_dict() for doc in docs]
    df = pd.DataFrame(data)
    if not df.empty and 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        # Säkra numeriska kolumner
        for col in ['readiness', 'sleep_score', 'vo2max', 'löpvolym_km', 'resting_hr', 'hrv_avg', 'ctl_fitness', 'atl_fatigue']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        # Beräkna Form (TSB = Fitness - Fatigue) om det saknas
        if 'ctl_fitness' in df.columns and 'atl_fatigue' in df.columns:
            df['tsb_form'] = df['ctl_fitness'] - df['atl_fatigue']
        else:
            # Fallback-estimering om avancerad belastning saknas ännu
            df['ctl_fitness'] = df['löpvolym_km'].rolling(14, min_periods=1).mean() * 10 + 40
            df['atl_fatigue'] = df['löpvolym_km'].rolling(3, min_periods=1).mean() * 12 + 45
            df['tsb_form'] = df['ctl_fitness'] - df['atl_fatigue']

    return df

df = load_data()

if df.empty:
    st.info("Ingen data hittades i Firebase. Kör garmin_coach.py för att ladda upp första mätningen.")
    st.stop()

latest = df.iloc[-1]

# --- 5. TOPP-HEADER (Mål & Prognoser) ---
st.markdown("""
<div style="margin-bottom: 15px;">
    <div style="font-size: 22px; font-weight: 800; color: #f0f6fc;">JÄGARTJÄNST / MÖNSTRING PR-PREPARATION</div>
    <div style="font-size: 12px; color: #8b949e;">
        <b>Mönstring & Jägartest:</b> Ca 45 dagar kvar &nbsp;|&nbsp; 
        <b>Garmin Prognos:</b> 5K: 18:30 &nbsp;•&nbsp; 10K: 38:15 &nbsp;•&nbsp; Cykeltest: Max &nbsp;•&nbsp; ISOK: Max (Nivå 9)
    </div>
</div>
""", unsafe_allow_html=True)

# --- 6. 8 KPI-KORT I RAD (Exakt som videon) ---
col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)

fitness_val = latest.get('ctl_fitness', 65.3)
fatigue_val = latest.get('atl_fatigue', 91.7)
form_val = latest.get('tsb_form', -26.4)
vol_val = df['löpvolym_km'].tail(7).sum() if 'löpvolym_km' in df.columns else 0.0
vo2_val = latest.get('vo2max', 56.0)
rhr_val = latest.get('resting_hr', 40)
hrv_val = latest.get('hrv_avg', 91)
sleep_val = latest.get('sleep_score', 79)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Fitness</div>
        <div class="metric-value">{fitness_val:.1f}</div>
        <div class="metric-sub metric-sub-green">▲ 7-day trend</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Fatigue</div>
        <div class="metric-value">{fatigue_val:.1f}</div>
        <div class="metric-sub metric-sub-red">▲ 7-day load</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    form_color = "metric-sub-red" if form_val < -10 else "metric-sub-green"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Form</div>
        <div class="metric-value">{form_val:.1f}</div>
        <div class="metric-sub {form_color}">4-day avg</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Weekly Vol</div>
        <div class="metric-value">{vol_val:.1f} <span style="font-size:14px;">km</span></div>
        <div class="metric-sub">7-day total</div>
    </div>
    """, unsafe_allow_html=True)

with col5:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">VO2 Max</div>
        <div class="metric-value">{vo2_val}</div>
        <div class="metric-sub metric-sub-green">Top 1%</div>
    </div>
    """, unsafe_allow_html=True)

with col6:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Resting HR</div>
        <div class="metric-value">{rhr_val} <span style="font-size:14px;">bpm</span></div>
        <div class="metric-sub">7-day avg</div>
    </div>
    """, unsafe_allow_html=True)

with col7:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">HRV Avg</div>
        <div class="metric-value">{hrv_val} <span style="font-size:14px;">ms</span></div>
        <div class="metric-sub metric-sub-green">Balanced</div>
    </div>
    """, unsafe_allow_html=True)

with col8:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Sleep Score</div>
        <div class="metric-value">{sleep_val}</div>
        <div class="metric-sub">Last night</div>
    </div>
    """, unsafe_allow_html=True)

# --- 7. SMART CALLOUTS & DAGENS REKOMMENDATION ---
c_left, c_right = st.columns([1, 1])

with c_left:
    st.markdown(f"""
    <div class="callout-box">
        <div class="callout-title">TODAY: {latest['date'].strftime('%A %d %b').upper()} • READINESS {latest.get('readiness', 'HIGH')}</div>
        <div class="callout-text">
            <b>Status:</b> Dagens rekommendation är ett kvalitetspass på cykel eller ISOK-styrka. Din återhämtning tillåter hög belastning, men se till att fylla på med näring inför morgondagens marschpass.
        </div>
    </div>
    """, unsafe_allow_html=True)

with c_right:
    if form_val < -15:
        st.markdown(f"""
        <div class="callout-box" style="border-left-color: #f85149;">
            <div class="callout-title">⚠️ BACK OFF FOR A FEW DAYS</div>
            <div class="callout-text">
                Din Form ({form_val:.1f}) är låg eftersom din träningsbelastning (Fatigue {fatigue_val:.1f}) ligger betydligt högre än din grundkondition (Fitness {fitness_val:.1f}). Prioritera sömn och zon 2 för att undvika överbelastning.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="callout-box" style="border-left-color: #3fb950;">
            <div class="callout-title">✅ GREEN LIGHT — OPTIMAL TRAINING ZONE</div>
            <div class="callout-text">
                Din Form och återhämtning är i balans. Du är i en idealisk fas för att driva på med tunga marklyft, intervaller och Jägartest-simuleringar.
            </div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# --- 8. FLIKAR LIKT VIDEON ---
tab_plan, tab_fitness, tab_effort, tab_recovery, tab_runs, tab_chat = st.tabs([
    "Plan", "Fitness", "Effort", "Recovery", "Runs", "💬 PT-Chatt"
])

# --- FLIK: PLAN (10-dagars översikt) ---
with tab_plan:
    st.markdown("**NEXT 10 DAYS SCHEDULE**")
    days_cols = st.columns(7)
    
    plan_days = [
        {"day": "MÅN", "title": "ISOK-Styrka", "detail": "Marklyft 5x5 + Benpress", "type": "hard"},
        {"day": "TIS", "title": "VO2-Max Cykel", "detail": "4x4 min @ 90-95% HR", "type": "hard"},
        {"day": "ONS", "title": "Aktiv Vila", "detail": "Rörlighet + Promenade", "type": "rest"},
        {"day": "TOR", "title": "Jägartest", "detail": "15 min Step-up 15kg + Chins", "type": "hard"},
        {"day": "FRE", "title": "Explosivitet", "detail": "Frivändningar + Bål", "type": "easy"},
        {"day": "LÖR", "title": "Marsch 10km", "detail": "15 kg ryggsäck (Zon 2)", "type": "easy"},
        {"day": "SÖN", "title": "Total Vila", "detail": "Sömn & Återhämtning", "type": "rest"}
    ]
    
    for idx, d in enumerate(plan_days):
        with days_cols[idx]:
            badge_class = f"badge-{d['type']}"
            st.markdown(f"""
            <div class="day-card">
                <div class="day-header">{d['day']}</div>
                <div class="day-title">{d['title']}</div>
                <div class="day-detail">{d['detail']}</div>
                <br>
                <span class="{badge_class}">{d['type'].upper()}</span>
            </div>
            """, unsafe_allow_html=True)

# --- FLIK: FITNESS (Grafer över CTL/ATL/TSB) ---
with tab_fitness:
    st.markdown("**FITNESS & FATIGUE OVER TIME (CTL / ATL / TSB)**")
    
    fig_fit = go.Figure()
    fig_fit.add_trace(go.Scatter(x=df['date'], y=df['ctl_fitness'], name='Fitness (CTL)', line=dict(color='#58a6ff', width=3)))
    fig_fit.add_trace(go.Scatter(x=df['date'], y=df['atl_fatigue'], name='Fatigue (ATL)', line=dict(color='#f85149', width=2)))
    
    fig_fit.update_layout(
        template="plotly_dark",
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        margin=dict(l=0, r=0, t=10, b=0),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_fit, use_container_width=True)

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        st.markdown("**FORM (TSB)**")
        colors = ['#3fb950' if val >= 0 else '#f85149' for val in df['tsb_form']]
        fig_form = go.Figure(go.Bar(x=df['date'], y=df['tsb_form'], marker_color=colors))
        fig_form.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=220, margin=dict(l=0,r=0,t=10,b=0))
        st.plotly_chart(fig_form, use_container_width=True)

    with col_f2:
        st.markdown("**WEEKLY VOLUME (KM)**")
        fig_vol = px.bar(df, x='date', y='löpvolym_km', color_discrete_sequence=['#58a6ff'])
        fig_vol.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=220, margin=dict(l=0,r=0,t=10,b=0))
        st.plotly_chart(fig_vol, use_container_width=True)

# --- FLIK: RECOVERY ---
with tab_recovery:
    st.markdown("**HEART RATE VARIABILITY & RECOVERY TRENDS**")
    
    fig_rec = px.line(df, x='date', y=[c for c in ['readiness', 'sleep_score'] if c in df.columns],
                      color_discrete_sequence=['#3fb950', '#58a6ff'], markers=True)
    fig_rec.update_layout(template="plotly_dark", plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", height=300, margin=dict(l=0,r=0,t=10,b=0))
    st.plotly_chart(fig_rec, use_container_width=True)

# --- FLIK: RUNS (Tabell) ---
with tab_runs:
    st.markdown("**RECENT WORKOUTS & ACTIVITIES**")
    st.dataframe(df[['date', 'löpvolym_km', 'readiness', 'sleep_score', 'vo2max']], use_container_width=True)

# --- FLIK: CHATT ---
with tab_chat:
    st.markdown("**MILITARY PT — INTERACTIVE ADVISORY**")
    if not GEMINI_API_KEY:
        st.error("Gemini API-nyckel saknas i secrets.")
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
Här är användarens senaste mätvärden (inkl Fitness/Fatigue/Form): {context_data}

Svara kortfattat, auktoritärt och konstruktivt på svenska.

Fråga: {prompt}"""

            with st.chat_message("assistant"):
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    response = client.models.generate_content(
                        model="gemini-3.5-flash",
                        contents=full_prompt
                    )
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Kunde inte hämta svar från Gemini: {e}")
