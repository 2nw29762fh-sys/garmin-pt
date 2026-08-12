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

# Tvinga Streamlit att använda sidbredden maximalt
st.set_page_config(page_title="Jägar-PT", page_icon="🪖", layout="wide", initial_sidebar_state="collapsed")

# --- CUSTOM CSS FÖR ATT EFTERLIKNA VIDEON (DARK MODE & KORT) ---
st.markdown("""
<style>
    /* Bakgrundsfärg och text */
    .stApp {
        background-color: #121212;
        color: #E0E0E0;
    }
    
    /* Skapa "Kort"-designen från videon */
    div.css-1r6slb0, div.css-12w0qpk {
        background-color: #1E1E1E;
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        border: 1px solid #333;
    }
    
    /* Styla mätvärdena (Metrics) */
    div[data-testid="stMetricValue"] {
        font-size: 28px !important;
        font-weight: 700 !important;
        color: #FFFFFF !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 12px !important;
        color: #A0A0A0 !important;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    
    /* Justera flikarna */
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 4px 4px 0px 0px;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
        color: #A0A0A0;
    }
    .stTabs [aria-selected="true"] {
        color: #FFFFFF !important;
        border-bottom-color: #3b82f6 !important;
    }
</style>
""", unsafe_allow_html=True)

# --- 2. INITIERA FIREBASE ---
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

# --- 3. LADDA DATA ---
@st.cache_data(ttl=30)
def load_data():
    docs = db.collection("garmin_history").stream()
    data = [doc.to_dict() for doc in docs]
    df = pd.DataFrame(data)
    if not df.empty and 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        # Konvertera relevanta kolumner till siffror
        for col in ['readiness', 'sleep_score', 'vo2max', 'löpvolym_km']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

df = load_data()

# --- 4. HEADER ---
st.markdown("### 🪖 Jägar-PT Analytics")
st.markdown("---")

if df.empty:
    st.info("Ingen data hittades i Firebase.")
    st.stop()

latest = df.iloc[-1]
date_str = latest['date'].strftime('%A %d %B, %Y').upper()

# --- 5. FLIKAR (Likt videon) ---
tab_plan, tab_fitness, tab_recovery, tab_runs, tab_chat = st.tabs([
    "PLAN", "FITNESS", "RECOVERY", "RUNS", "💬 PT-CHATT"
])

# --- FLIK: RECOVERY (Återhämtning & Sömn) ---
with tab_recovery:
    st.markdown(f"**TODAY • {date_str}**")
    
    # Skapa kort för mätvärden högst upp
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("TRAINING READINESS", f"{latest.get('readiness', 'N/A')}", latest.get('readiness_level', ''))
    with col2:
        st.metric("SLEEP SCORE", f"{latest.get('sleep_score', 'N/A')} / 100")
    with col3:
        st.metric("VO2 MAX", f"{latest.get('vo2max', 'N/A')}")
    with col4:
        st.metric("7-DAY VOLUME", f"{df['löpvolym_km'].tail(7).sum():.1f} km")

    st.markdown("<br>", unsafe_allow_html=True)
    
    # Mörka Plotly-grafer
    col_graph1, col_graph2 = st.columns(2)
    
    with col_graph1:
        st.markdown("**Readiness vs Sleep (Trend)**")
        plot_cols = [c for c in ['readiness', 'sleep_score'] if c in df.columns]
        fig_rec = px.line(df, x='date', y=plot_cols, color_discrete_sequence=['#3b82f6', '#10b981'])
        fig_rec.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title="", yaxis_title="Score"
        )
        st.plotly_chart(fig_rec, use_container_width=True)
        
    with col_graph2:
        st.markdown("**Running Volume (km)**")
        if 'löpvolym_km' in df.columns:
            fig_vol = px.bar(df, x='date', y='löpvolym_km', color_discrete_sequence=['#ef4444'])
            fig_vol.update_layout(
                template="plotly_dark",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="", yaxis_title="Distance (km)"
            )
            st.plotly_chart(fig_vol, use_container_width=True)

# --- FLIK: FITNESS & RUNS (Kombinerad rådata för nu) ---
with tab_fitness:
    st.markdown("**Fitness Metrics & Raw Data Log**")
    st.dataframe(df, use_container_width=True)

with tab_runs:
    st.markdown("**Recent Runs**")
    # Här kan du filtrera ut enbart löppassen i framtiden
    runs_df = df[df['löpvolym_km'] > 0] if 'löpvolym_km' in df.columns else pd.DataFrame()
    if not runs_df.empty:
        st.dataframe(runs_df[['date', 'löpvolym_km']], use_container_width=True)
    else:
        st.info("Inga löppass registrerade ännu.")

with tab_plan:
    st.markdown("**Training Plan**")
    st.info("Fråga PT:n i chatten för att få fram veckans strategiska stridsplan.")

# --- FLIK: INTERAKTIV CHATT ---
with tab_chat:
    st.markdown("**Military PT - Intelligence & Advisory**")
    
    if not GEMINI_API_KEY:
        st.error("Gemini API-nyckel saknas i miljövariablerna.")
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
            full_prompt = f"""Du är en stenhård men pedagogisk militär PT. Din expertis är mönstring, Jägartestet och Försvarsmaktens fysiska krav.
Här är användarens senaste Garmin-data: {context_data}

Svara på användarens fråga baserat på deras data. Var kortfattad, auktoritär men konstruktiv.

Användarens fråga: {prompt}"""

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
