import os
import json
import streamlit as st
import pandas as pd
import plotly.express as px
from google import genai
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# 1. Inställningar & Miljö
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Initiera Firebase för både lokal körning och molnet
if not firebase_admin._apps:
    fb_creds = os.environ.get("FIREBASE_CREDENTIALS")
    if fb_creds:
        # Körs i molnet (läser från en hemlig textsträng)
        cred_dict = json.loads(fb_creds)
        cred = credentials.Certificate(cred_dict)
    else:
        # Körs lokalt på din Mac (läser från filen)
        KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")
        if os.path.exists(KEY_PATH):
            cred = credentials.Certificate(KEY_PATH)
        else:
            st.error("Hittade inga inloggningsuppgifter till Firebase.")
            st.stop()
    firebase_admin.initialize_app(cred)

db = firestore.client()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

st.set_page_config(page_title="Jägar-PT Dashboard", page_icon="🏃‍♂️", layout="wide")

# 2. Ladda data från FIREBASE istället för history.json
@st.cache_data(ttl=30)
def load_data():
    docs = db.collection("garmin_history").stream()
    data = [doc.to_dict() for doc in docs]
    df = pd.DataFrame(data)
    if not df.empty and 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
    return df

df = load_data()

# 3. UI - Header
st.title("🏃‍♂️ Jägar-PT - Pro Dashboard")

# 4. Flikar
tab1, tab2, tab3 = st.tabs(["📊 Trender & Status", "📋 Historik", "💬 Chatta med Jägar-PT"])

# --- FLIK 1: TRENDER & STATUS ---
with tab1:
    if df.empty:
        st.info("Ingen data hittades i Firebase.")
    else:
        latest = df.iloc[-1]
        st.subheader(f"Status för {latest['date'].strftime('%Y-%m-%d')}")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Readiness", f"{latest.get('readiness', 'N/A')}", latest.get('readiness_level', ''))
        c2.metric("Sömnpoäng", f"{latest.get('sleep_score', 'N/A')} / 100")
        c3.metric("VO2max", f"{latest.get('vo2max', 'N/A')}")
        c4.metric("Löpvolym (Dag)", f"{latest.get('löpvolym_km', 0)} km")

        st.markdown("---")
        
        col_left, col_right = st.columns(2)
        df_plot = df.copy()
        for col in ['readiness', 'sleep_score', 'vo2max', 'löpvolym_km']:
            if col in df_plot.columns:
                df_plot[col] = pd.to_numeric(df_plot[col], errors='coerce')

        with col_left:
            plot_cols = [c for c in ['readiness', 'sleep_score'] if c in df_plot.columns]
            fig_rec = px.line(df_plot, x='date', y=plot_cols, markers=True, title="Återhämtningstrender")
            st.plotly_chart(fig_rec, use_container_width=True)
            
        with col_right:
            if 'löpvolym_km' in df_plot.columns:
                fig_vol = px.bar(df_plot, x='date', y='löpvolym_km', title="Löpvolym per dag (km)")
                st.plotly_chart(fig_vol, use_container_width=True)

# --- FLIK 2: RÅDATA ---
with tab2:
    st.subheader("Träningslogg från molnet")
    st.dataframe(df, use_container_width=True)

# --- FLIK 3: INTERAKTIV CHATT ---
with tab3:
    st.subheader("Militär PT - Frågor & Svar")
    
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
