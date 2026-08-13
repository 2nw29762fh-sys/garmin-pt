#!/usr/bin/env python3
"""
FafoCoach-lite (Firebase-edition)
---------------------------------
Hämtar Garmin-data, sparar trender till Firebase Firestore och ber Gemini 
om ett militärinriktat träningsupplägg (Jägartest/Mönstring).
"""

import os
import sys
import getpass
from datetime import date, timedelta
import json
import requests

from dotenv import load_dotenv
from garminconnect import Garmin
from google import genai

import firebase_admin
from firebase_admin import credentials, firestore

# Sätt sökvägar (explicit för launchd)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

MODEL = "gemini-3.5-flash"
DAYS_BACK = 7
TOKENSTORE = os.path.expanduser("~/.garminconnect")

# --- INITIERA FIREBASE ---
KEY_PATH = os.path.join(SCRIPT_DIR, "firebase-key.json")

if not firebase_admin._apps:
    if not os.path.exists(KEY_PATH):
        print(f"Fel: Hittar inte nyckelfilen för Firebase på {KEY_PATH}")
        sys.exit(1)
    cred = credentials.Certificate(KEY_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()

def get_credentials():
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    api_key = os.environ.get("GEMINI_API_KEY")

    if not (email and password and api_key) and sys.stdin.isatty():
        email = email or input("Garmin e-post: ")
        password = password or getpass.getpass("Garmin lösenord: ")
        api_key = api_key or getpass.getpass("Gemini API-nyckel: ")
        
    return email, password, api_key

def fetch_garmin_data(email, password):
    print("Loggar in på Garmin Connect...")
    client = Garmin(email, password)
    client.login()

    today = date.today()
    start_date = today - timedelta(days=DAYS_BACK)
    yesterday = today - timedelta(days=1)

    print(f"Hämtar data {start_date} -> {today} ...")
    
    data = {}
    data["activities"] = client.get_activities_by_date(start_date.isoformat(), today.isoformat(), "")
    
    try:
        # Försöker först hämta natten som precis var (today)
        sleep_data_today = client.get_sleep_data(today.isoformat())
        
        # Säkerställer att datan faktiskt innehåller en sömnpoäng (så den inte bara är en tom mall)
        if sleep_data_today and sleep_data_today.get("dailySleepDTO", {}).get("sleepScores"):
            data["sleep"] = sleep_data_today
        else:
            # Fallback till gårdagens sömn om dagens inte har synkat än
            data["sleep"] = client.get_sleep_data(yesterday.isoformat())
    except:
        data["sleep"] = None
        
    try:
        data["training_readiness"] = client.get_training_readiness(today.isoformat())
    except:
        data["training_readiness"] = None
        
    try:
        data["training_status"] = client.get_training_status(today.isoformat())
        data["hrv"] = client.get_hrv_data(today.isoformat())
    except:
        pass

    return data

def summarize_for_prompt(data):
    lines = []
    lines.append("AKTIVITETER (senaste 7 dagarna):")
    activities = data.get("activities", [])
    
    running_volume_km = 0.0
    
    if not activities:
        lines.append("- Inga registrerade aktiviteter.")
    else:
        for act in activities:
            name = act.get('activityName', 'Okänd')
            dist = act.get('distance', 0) / 1000
            lines.append(f"- {name}: {dist:.1f} km")
            act_type = act.get('activityType', {}).get('typeKey', '')
            if act_type == 'running':
                running_volume_km += dist
    
    # Sömn
    overall_sleep = "N/A"
    if data.get("sleep"):
        sleep_dto = data["sleep"].get("dailySleepDTO", {})
        overall_sleep = sleep_dto.get("sleepScores", {}).get("overall", {}).get("value", "N/A")
        sleep_time = sleep_dto.get("sleepTimeSeconds")
        
        lines.append("\nSENASTE REGISTRERADE SÖMN:")
        if sleep_time:
            lines.append(f"- Total sömn: {sleep_time // 60} min, sömnpoäng: {overall_sleep}")
        else:
            lines.append(f"- Sömnpoäng: {overall_sleep}")

    # Readiness
    tr_score = "N/A"
    tr_level = "N/A"
    if data.get("training_readiness"):
        tr_data = data["training_readiness"]
        if isinstance(tr_data, list):
            tr = tr_data[0] if len(tr_data) > 0 else {}
        else:
            tr = tr_data
            
        if isinstance(tr, dict):
            tr_score = tr.get('score', 'N/A')
            tr_level = tr.get('level', 'N/A')
            
        lines.append("\nTRAINING READINESS:")
        lines.append(f"- Poäng: {tr_score}, nivå: {tr_level}")

    # HRV & Status
    if data.get("training_status"):
        lines.append("\nTRAINING STATUS (rådata, trunkerad):")
        lines.append(str(data["training_status"])[:500])

    if data.get("hrv"):
        lines.append("\nHRV (rådata, trunkerad):")
        lines.append(str(data["hrv"])[:300])

vo2_val = "N/A"
    try:
        vo2_val = data.get("training_status", {}).get("mostRecentVO2Max", {}).get("generic", {}).get("vo2MaxValue", "N/A")
    except:
        pass

    # --- HÄMTA VILOPULS & HRV ---
    rhr = "N/A"
    hrv_val = "N/A"
    try:
        rhr = data.get("hrv", {}).get("hrvSummary", {}).get("baseline", {}).get("balancedLow", 40)
        hrv_val = data.get("hrv", {}).get("hrvSummary", {}).get("lastNightAvg", 91)
    except:
        pass

    # --- SPARA TILL FIREBASE FIRESTORE ---
    today_str = date.today().isoformat()
    dagens_data = {
        "date": today_str, 
        "readiness": tr_score,
        "readiness_level": tr_level,
        "vo2max": vo2_val,  
        "sleep_score": overall_sleep,
        "workout_count": len(activities),
        "löpvolym_km": round(running_volume_km, 1),
        "resting_hr": rhr if isinstance(rhr, (int, float)) else 40,
        "hrv_avg": hrv_val if isinstance(hrv_val, (int, float)) else 91,
        "ctl_fitness": 65.3,  
        "atl_fatigue": 91.7   
    }
    
    try:
        db.collection("garmin_history").document(today_str).set(dagens_data)
        print(f"Laddade upp dagens mätning ({today_str}) till Firebase Firestore!")
    except Exception as e:
        print(f"Kunde inte spara till Firebase: {e}")

def ask_gemini(api_key, summary):
    client = genai.Client(api_key=api_key)

    prompt = f"""Du är en elitcoach med inriktning på militär fysträning och förberedelser inför Försvarsmakten.
Ditt uppdrag är att göra mig redo att få maxpoäng på mönstringens fystester (ISOK-maskinen och cykeltestet) 
samt att bygga den uthållighet och råstyrka som krävs för att utan problem klara kraven för Jägarsoldat (inklusive Jägartestet/step-test med 20 kg packning, tunga marscher och kroppsviktsstyrka).

Analysera följande Garmin-data och ge ett konkret träningsupplägg för kommande vecka.
Ta hänsyn till min återhämtning (training readiness/sömn) innan du föreslår hårda pass.

Styr passen mot dessa tre pelare:
1. Uthållighet/VO2-max (för att krossa cykeltestet/löpningen).
2. Tung ben/bål-styrka, särskilt marklyft och benpress (för att maxa ISOK-testet).
3. Muskulär uthållighet, marsch med ryggsäck och chins/armhävningar (för Jägartestet).

Var specifik med passens genomförande och belastning, men håll svaret kort och kärnfullt.

GARMIN-DATA:
{summary}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )
    return response.text

def send_telegram(text):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        try:
            requests.post(url, json=payload)
            print("Skickade resultatet till Telegram.")
        except Exception as e:
            print(f"Kunde inte skicka till Telegram: {e}")

def main():
    email, password, api_key = get_credentials()
    
    if not all([email, password, api_key]):
        print("Fel: Saknar inloggningsuppgifter (Garmin eller Gemini).")
        sys.exit(1)
        
    data = fetch_garmin_data(email, password)
    summary = summarize_for_prompt(data)

    print("\n----- SAMMANFATTNING SOM SKICKAS TILL GEMINI -----")
    print(summary)

    print("\nFrågar Gemini om träningsupplägg...\n")
    advice = ask_gemini(api_key, summary)

    print("----- GEMINIS TRÄNINGSUPPLÄGG -----\n")
    print(advice)
    
    send_telegram(advice)

if __name__ == "__main__":
    main()
