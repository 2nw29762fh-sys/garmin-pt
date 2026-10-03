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

    try:
        data["stats"] = client.get_stats(date.today().isoformat())
    except:
        data["stats"] = {}

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
        raw_vo2 = data.get("training_status", {}).get("mostRecentVO2Max", {}).get("generic", {}).get("vo2MaxPreciseValue", "N/A")
        vo2_val = round(float(raw_vo2), 1) if raw_vo2 != "N/A" else "N/A"    
    except:
        pass

    # --- HÄMTA VILOPULS & HRV ---
    rhr = "N/A"
    hrv_val = "N/A"
    try:
        rhr = data.get("stats", {}).get("restingHeartRate", "N/A")
        hrv_val = data.get("hrv", {}).get("hrvSummary", {}).get("lastNightAvg", 91)
    except:
        pass

    # --- SPARA TILL FIREBASE FIRESTORE ---
    # --- NYTT: HÄMTA DETALJERAD SÖMN, STRESS, BODY BATTERY OCH STEG ---
    deep_sleep_min = 0
    rem_sleep_min = 0
    bb_max = "N/A"
    bb_min = "N/A"
    stress_avg = "N/A"
    steps = 0
    
    try:
        # Detaljerad sömn (Djupsömn och REM i minuter)
        if data.get("sleep"):
            sleep_dto = data["sleep"].get("dailySleepDTO", {})
            deep_sleep_min = sleep_dto.get("deepSleepSeconds", 0) // 60
            rem_sleep_min = sleep_dto.get("remSleepSeconds", 0) // 60
            
        # Daglig statistik (Body Battery, Stress, Steg)
        if data.get("stats"):
            stats = data["stats"]
            bb_max = stats.get("bodyBatteryHighestValue", "N/A")
            bb_min = stats.get("bodyBatteryLowestValue", "N/A")
            stress_avg = stats.get("averageStressLevel", "N/A")
            steps = stats.get("totalSteps", 0)
    except Exception as e:
        print(f"Kunde inte hämta utökad data: {e}")

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
        "atl_fatigue": 91.7,
        "deep_sleep_min": deep_sleep_min,
        "rem_sleep_min": rem_sleep_min,
        "bb_max": bb_max,
        "bb_min": bb_min,
        "stress_avg": stress_avg,
        "steps": steps
    }
    
    try:
        db.collection("garmin_history").document(today_str).set(dagens_data)
        print(f"Laddade upp dagens mätning ({today_str}) till Firebase Firestore!")
    except Exception as e:
        print(f"Kunde inte spara till Firebase: {e}")

    return "\n".join(lines)

# --- 1. API-ANROP: EXTRAHERA AVANCERAD DATA ---
def extract_advanced_metrics(data):
    """Hämtar Readiness, HRV och Load Ratio från din redan nedladdade Garmin-data."""
    metrics = {
        "readiness_score": "N/A",
        "hrv_status": "N/A",
        "acute_load": "N/A",
        "chronic_load": "N/A",
        "load_ratio": "N/A"
    }
    
    tr = data.get("training_readiness")
    if isinstance(tr, list) and len(tr) > 0:
        metrics["readiness_score"] = tr[0].get("score", "N/A")
    elif isinstance(tr, dict):
        metrics["readiness_score"] = tr.get("score", "N/A")

    hrv = data.get("hrv")
    if hrv and "hrvSummary" in hrv:
        metrics["hrv_status"] = hrv["hrvSummary"].get("status", "N/A")

    ts = data.get("training_status")
    if ts:
        metrics["acute_load"] = ts.get("mostRecentAcuteLoad", "N/A")
        metrics["chronic_load"] = ts.get("mostRecentChronicLoad", "N/A")
        if isinstance(metrics["acute_load"], (int, float)) and isinstance(metrics["chronic_load"], (int, float)):
            if metrics["chronic_load"] > 0:
                metrics["load_ratio"] = round(metrics["acute_load"] / metrics["chronic_load"], 2)
                
    return metrics


# --- 2. TELEGRAM-INTEGRATION: LYSSNA PÅ MANUELLA PASS ---
def fetch_and_log_telegram_workouts(bot_token, db):
    """Hämtar dina manuellt skrivna pass i Telegram och sparar i Firestore."""
    import requests
    import datetime
    
    state_ref = db.collection("system").document("telegram_state")
    state_doc = state_ref.get()
    
    last_update_id = 0
    if state_doc.exists:
        last_update_id = state_doc.to_dict().get("last_update_id", 0)
    
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={last_update_id + 1}"
    new_workouts = []
    
    try:
        response = requests.get(url).json()
        if response.get("ok") and response.get("result"):
            for item in response["result"]:
                update_id = item["update_id"]
                msg = item.get("message", {})
                text = msg.get("text", "")
                
                if text and not text.startswith("/"):
                    workout_data = {
                        "beskrivning": text,
                        "timestamp": msg.get("date"),
                        "logged_at": datetime.datetime.now().isoformat()
                    }
                    db.collection("manual_workouts").document(str(update_id)).set(workout_data)
                    new_workouts.append(text)
                    print(f"Loggade nytt pass från Telegram: {text}")
                
                last_update_id = update_id
            
            state_ref.set({"last_update_id": last_update_id})
    except Exception as e:
        print(f"Kunde inte hämta pass från Telegram: {e}")
        
    return new_workouts


# --- 3. AI-PROMPTING: JÄGAR-PT ---
def generate_daily_jagar_plan(api_key, garmin_metrics, recent_workouts):
    """Låter Gemini analysera allt och ge en daglig stridsorder."""
    client = genai.Client(api_key=api_key)
    
    manual_workouts_str = "\n".join([f"- {w}" for w in recent_workouts])
    if not manual_workouts_str:
        manual_workouts_str = "- Inga manuellt loggade pass (BJJ/Kroppsvikt) sedan senaste körningen."

    prompt = f"""Du är en extremt analytisk och stenhård militär fystränare. Ditt uppdrag är att förbereda användaren för mönstring och Försvarsmaktens Jägartester.
Ditt mål är att maximera användarens kapacitet i cykeltestet (VO2-max), ISOK-maskinen (maxstyrka i ben/rygg) och muskulär uthållighet, UTAN att driva användaren in i överträning.

Du har här tillgång till dagens Garmin-data och eventuella manuellt loggade pass (ex. BJJ/kroppsvikt) som Garmin missat.

ANVÄNDARENS STATUS IDAG:
- Training Readiness: {garmin_metrics.get('readiness_score')} (0-100)
- HRV Status: {garmin_metrics.get('hrv_status')}
- Acute/Chronic Load Ratio: {garmin_metrics.get('load_ratio')} (Optimalt ligger runt 0.8-1.3)
- Nyligen loggade pass (utanför Garmin):
{manual_workouts_str}

UPPGIFT:
Ge en datadriven, daglig rekommendation. Baserat på statusen, beordra vilket av följande användaren ska fokusera på idag:
1. KONDITION (Löpning/Cykel för VO2-max eller aerob bas)
2. MUSKULÄR UTHÅLLIGHET/STYRKA (ISOK-fokus, jägar-step-ups, chins/armhävningar)
3. VILA / AKTIV ÅTERHÄMTNING (Om Readiness är låg, HRV är obalanserad eller Load Ratio är för hög)

Formulera svaret enligt följande struktur (Svara på svenska):
- KORT ANALYS: (1-2 meningar om vad datan betyder idag).
- DAGENS ORDER: (Vilket fokus och varför).
- FÖRESLAGET PASS: (Specifikt och kärnfullt pass, ex. intervaller, styrkeschema eller vila. Ange duration och intensitet).
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt
    )
    return response.text


# --- SÄNDARE & HUVUDMOTOR ---
def send_telegram(text): 
    import os, requests
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
    import sys, os
    email, password, api_key = get_credentials()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not all([email, password, api_key]):
        print("Fel: Saknar inloggningsuppgifter (Garmin eller Gemini).")
        sys.exit(1)

    print("Hämtar data från Garmin Connect...")
    data = fetch_garmin_data(email, password)
    
    print("Sparar basdata till databasen...")
    summarize_for_prompt(data) # Körs för att spara historik i db
    
    print("Analyserar avancerade mätvärden (Readiness/HRV/Load)...")
    garmin_metrics = extract_advanced_metrics(data)
    
    print("Kollar Telegram efter BJJ och kroppsviktspass...")
    recent_workouts = fetch_and_log_telegram_workouts(bot_token, db)

    print("\nFrågar Gemini (Jägar-PT) om dagens order...\n")
    advice = generate_daily_jagar_plan(api_key, garmin_metrics, recent_workouts)

    print("----- GEMINIS DAGLIGA ORDER -----\n")
    print(advice)
    
    send_telegram(advice)

if __name__ == "__main__":
    main()
