"""
Fitness Coach Agent
"""

import os
import json
import logging
from datetime import datetime, timedelta
from supabase import create_client
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
SUPABASE_URL     = os.environ["SUPABASE_URL"]
SUPABASE_KEY     = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID  = int(os.environ.get("ALLOWED_USER_ID", "0"))

PRICE_INPUT_PER_M  = 3.00
PRICE_OUTPUT_PER_M = 15.00

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

supabase  = create_client(SUPABASE_URL, SUPABASE_KEY)
anthropic = Anthropic(api_key=ANTHROPIC_KEY)

SYSTEM_PROMPT = """Sei un coach fitness personale professionale e preciso.
Il tuo nome è Coach Pro. Hai accesso ai dati reali dell'utente (Simone).
Stile: professionale, diretto, basato sui dati.
Analizza progressi, sonno, recupero, suggerisci allenamenti.
Obiettivo: mantenersi in forma.
Rispondi sempre in italiano."""

conversation_history: list = []
peso_state: dict = {}
basket_state: dict = {}
n_messaggi_sessione: int = 0

# ─────────────────────────────────────────────
# Funzioni Supabase
# ─────────────────────────────────────────────

def get_recent_metrics(days=14):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return supabase.table("daily_health_metrics").select("*").gte("date", since).order("date", desc=True).execute().data or []

def get_recent_workouts(days=30):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return supabase.table("workouts").select("*").gte("data_ora", since).order("data_ora", desc=True).execute().data or []

def get_recent_basket(days=30):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return supabase.table("basket_sessions").select("*").gte("data_ora", since).order("data_ora", desc=True).execute().data or []

def get_weight_trend(days=90):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return supabase.table("daily_health_metrics").select("date,peso_kg,grasso_corporeo_pct,massa_muscolare_kg,grasso_viscerale").gte("date", since).not_.is_("peso_kg", "null").order("date", desc=True).execute().data or []

def get_summary_stats():
    metrics = get_recent_metrics(days=30)
    if not metrics:
        return {}
    def avg(lst, key):
        vals = [x[key] for x in lst if x.get(key)]
        return round(sum(vals)/len(vals), 1) if vals else None
    return {
        "passi_medi":            avg(metrics, "passi"),
        "distanza_media_km":     avg(metrics, "distanza_km"),
        "fc_riposo_media":       avg(metrics, "freq_cardiaca_riposo"),
        "sonno_medio_hr":        avg(metrics, "sonno_totale_hr"),
        "sonno_profondo_medio":  avg(metrics, "sonno_profondo_hr"),
        "sonno_rem_medio":       avg(metrics, "sonno_rem_hr"),
        "vo2max_recente":        next((x["vo2max"] for x in metrics if x.get("vo2max")), None),
        "variabilita_fc_media":  avg(metrics, "variabilita_fc_ms"),
        "tempo_esercizio_medio": avg(metrics, "tempo_esercizio_min"),
        "grasso_corporeo":       next((x["grasso_corporeo_pct"] for x in metrics if x.get("grasso_corporeo_pct")), None),
        "massa_muscolare":       next((x["massa_muscolare_kg"] for x in metrics if x.get("massa_muscolare_kg")), None),
        "grasso_viscerale":      next((x["grasso_viscerale"] for x in metrics if x.get("grasso_viscerale")), None),
        "eta_metabolica":        next((x["eta_metabolica"] for x in metrics if x.get("eta_metabolica")), None),
    }

def build_context_for_claude(user_message, memoria=""):
    metrics_recenti = get_recent_metrics(days=14)
    workouts_recenti = get_recent_workouts(days=30)
    basket_recenti = get_recent_basket(days=30)
    peso_trend = get_weight_trend(days=90)
    stats = get_summary_stats()
    ultimi_7 = metrics_recenti[:7]
    righe = []
    for m in ultimi_7:
        righe.append(f"  {m['date']}: passi={m.get('passi','?')}, peso={m.get('peso_kg','?')}kg, grasso={m.get('grasso_corporeo_pct','?')}%, FC_riposo={m.get('freq_cardiaca_riposo','?')}bpm, sonno={m.get('sonno_totale_hr','?')}h, esercizio={m.get('tempo_esercizio_min','?')}min")
    pesi = [(x['date'], x['peso_kg']) for x in peso_trend if x.get('peso_kg')]
    peso_info = ""
    if len(pesi) >= 2:
        delta = round(pesi[0][1] - pesi[-1][1], 1)
        peso_info = f"Peso: {pesi[0][1]}kg ({'+' if delta>0 else ''}{delta}kg negli ultimi {len(pesi)} giorni)"
    workout_info = f"{len(workouts_recenti)} sessioni negli ultimi 30 giorni"
    if workouts_recenti:
        u = workouts_recenti[0]
        workout_info += f" | Ultimo: {u['data_ora'][:10]}, FC media {u.get('fc_media','?')}bpm"
    basket_info = f"{len(basket_recenti)} sessioni basket negli ultimi 30 giorni"
    if basket_recenti:
        b = basket_recenti[0]
        basket_info += f" | Ultima: {b['data_ora'][:10]}, {b.get('tipo','?')}, intensita {b.get('intensita','?')}/10"
    sezione_memoria = f"\n=== MEMORIA CONVERSAZIONI PRECEDENTI ===\n{memoria}\n" if memoria else ""
    return f"""
=== DATI REALI UTENTE ==={sezione_memoria}
STATISTICHE 30 GIORNI:
- Passi medi: {stats.get('passi_medi','N/D')}
- FC riposo: {stats.get('fc_riposo_media','N/D')} bpm
- Sonno: {stats.get('sonno_medio_hr','N/D')}h (REM: {stats.get('sonno_rem_medio','N/D')}h)
- VO2Max: {stats.get('vo2max_recente','N/D')} ml/kg/min
- HRV: {stats.get('variabilita_fc_media','N/D')} ms
- Esercizio medio: {stats.get('tempo_esercizio_medio','N/D')} min/gg
- Allenamenti palestra: {workout_info}
- Basket: {basket_info}
- Grasso corporeo: {stats.get('grasso_corporeo','N/D')}%
- Massa muscolare: {stats.get('massa_muscolare','N/D')} kg
- Grasso viscerale: {stats.get('grasso_viscerale','N/D')}
- Eta metabolica: {stats.get('eta_metabolica','N/D')} anni
PESO: {peso_info or 'N/D'}
ULTIMI 7 GIORNI:
{chr(10).join(righe) if righe else 'Nessun dato'}
=== MESSAGGIO ===
{user_message}"""

# ─────────────────────────────────────────────
# Memoria
# ─────────────────────────────────────────────

def carica_memoria():
    try:
        res = supabase.table("memoria").select("riassunto").order("created_at", desc=True).limit(1).execute()
        if res.data:
            return res.data[0]["riassunto"]
    except Exception as e:
        logger.error(f"Errore caricamento memoria: {e}")
    return ""

def salva_memoria(riassunto, n_msg):
    try:
        supabase.table("memoria").insert({"riassunto": riassunto, "n_messaggi": n_msg}).execute()
    except Exception as e:
        logger.error(f"Errore salvataggio memoria: {e}")

def genera_riassunto(storia):
    if not storia:
        return ""
    try:
        testo = "\n".join([f"{'Simone' if m['role']=='user' else 'Coach'}: {m['content'][:300]}" for m in storia])
        response = anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            messages=[{"role":"user","content":f"Riassumi questa conversazione tra Simone e il suo coach fitness in massimo 250 parole. Includi: obiettivi discussi, progressi, problemi, consigli, preferenze. In italiano, forma compatta.\n\n{testo}"}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Errore riassunto: {e}")
        return ""

# ─────────────────────────────────────────────
# Token
# ─────────────────────────────────────────────

def calcola_costo(i, o):
    return round((i/1_000_000)*PRICE_INPUT_PER_M + (o/1_000_000)*PRICE_OUTPUT_PER_M, 6)

def salva_token_usage(i, o, msg):
    try:
        supabase.table("token_usage").insert({"input_tokens":i,"output_tokens":o,"costo_usd":calcola_costo(i,o),"messaggio":msg[:100]}).execute()
    except Exception as e:
        logger.error(f"Errore token: {e}")

def get_stats_token(giorni=30):
    since = (datetime.now() - timedelta(days=giorni)).isoformat()
    try:
        rows = supabase.table("token_usage").select("*").gte("timestamp", since).execute().data or []
        if not rows: return {}
        tot_i = sum(r["input_tokens"] for r in rows)
        tot_o = sum(r["output_tokens"] for r in rows)
        tot_c = sum(r["costo_usd"] for r in rows)
        inizio = datetime.now().replace(day=1,hour=0,minute=0,second=0).isoformat()
        costo_mese = sum(r["costo_usd"] for r in rows if r["timestamp"] >= inizio)
        return {"n_messaggi":len(rows),"tot_token":tot_i+tot_o,"tot_input":tot_i,"tot_output":tot_o,"tot_costo_usd":round(tot_c,4),"costo_mese_usd":round(costo_mese,4),"costo_medio":round(tot_c/len(rows),4)}
    except Exception as e:
        logger.error(f"Errore stats token: {e}")
        return {}

# ─────────────────────────────────────────────
# Stima metriche basket con Claude
# ─────────────────────────────────────────────

def stima_metriche_basket(session_data: dict, peso_kg: float) -> dict:
    """Chiede a Claude di stimare le metriche fisiologiche della sessione basket."""
    try:
        prompt = f"""Sei un esperto di fisiologia dello sport. Stima le metriche fisiologiche per questa sessione di basket.

Dati utente:
- Peso: {peso_kg} kg
- Tipo sessione: {session_data.get('tipo')}
- {'Minuti giocati: ' + str(session_data.get('minuti_giocati')) if session_data.get('tipo') == 'partita' else 'Durata: ' + str(session_data.get('durata_min')) + ' min'}
- {'Risultato: ' + str(session_data.get('risultato')) if session_data.get('tipo') == 'partita' else 'Focus: ' + str(session_data.get('focus'))}
- Intensita percepita: {session_data.get('intensita')}/10
- Note: {session_data.get('note', 'nessuna')}

Rispondi SOLO con un JSON valido con questi campi:
{{
  "calorie_stimate": <int>,
  "fc_media_stimata": <int>,
  "fc_max_stimata": <int>,
  "distanza_stimata_km": <float>,
  "carico_allenamento": <int>,
  "spiegazione": "<stringa breve max 100 parole>"
}}

Per il basket:
- Una partita a intensita 7-8/10 brucia circa 600-800 kcal/ora
- FC media in partita e circa 155-175 bpm
- Distanza percorsa in partita e circa 5-7 km
- Allenamento tecnico brucia meno (400-500 kcal/ora)
- Carico allenamento = minuti x intensita"""

        response = anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=300,
            messages=[{"role":"user","content":prompt}]
        )
        text = response.content[0].text.strip()
        # Estrai JSON dalla risposta
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    except Exception as e:
        logger.error(f"Errore stima metriche basket: {e}")
        return {}

# ─────────────────────────────────────────────
# Gestione stato basket
# ─────────────────────────────────────────────

async def handle_basket_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    state = basket_state[user_id]
    step = state["step"]

    if text.lower() in ("annulla", "cancel"):
        del basket_state[user_id]
        await update.message.reply_text("Inserimento annullato.")
        return

    # Step 0: tipo sessione
    if step == "tipo":
        if text.lower() in ("1", "partita"):
            state["data"]["tipo"] = "partita"
            state["step"] = "minuti"
            await update.message.reply_text("Quanti minuti hai giocato?")
        elif text.lower() in ("2", "allenamento"):
            state["data"]["tipo"] = "allenamento"
            state["step"] = "durata"
            await update.message.reply_text("Durata allenamento in minuti?")
        else:
            await update.message.reply_text("Scrivi 1 per Partita o 2 per Allenamento:")
        return

    # Step partita
    if step == "minuti":
        try:
            state["data"]["minuti_giocati"] = int(text.strip())
            state["step"] = "risultato"
            await update.message.reply_text("Risultato?\n1 - Vinto\n2 - Perso")
        except ValueError:
            await update.message.reply_text("Inserisci un numero di minuti valido:")
        return

    if step == "risultato":
        if text.lower() in ("1", "vinto"):
            state["data"]["risultato"] = "vinto"
        elif text.lower() in ("2", "perso"):
            state["data"]["risultato"] = "perso"
        else:
            await update.message.reply_text("Scrivi 1 per Vinto o 2 per Perso:")
            return
        state["step"] = "intensita"
        await update.message.reply_text("Intensita percepita? (1-10)\n1=molto bassa, 10=massima")
        return

    # Step allenamento
    if step == "durata":
        try:
            state["data"]["durata_min"] = int(text.strip())
            state["step"] = "focus"
            await update.message.reply_text(
                "Focus dell'allenamento?\n"
                "1 - Tiro\n2 - Palleggio\n3 - Difesa\n4 - Fisico\n5 - Misto"
            )
        except ValueError:
            await update.message.reply_text("Inserisci un numero di minuti valido:")
        return

    if step == "focus":
        focus_map = {"1":"tiro","2":"palleggio","3":"difesa","4":"fisico","5":"misto"}
        focus = focus_map.get(text.strip(), text.lower())
        state["data"]["focus"] = focus
        state["step"] = "intensita"
        await update.message.reply_text("Intensita percepita? (1-10)\n1=molto bassa, 10=massima")
        return

    # Step comune: intensita
    if step == "intensita":
        try:
            val = int(text.strip())
            if not 1 <= val <= 10:
                raise ValueError
            state["data"]["intensita"] = val
            state["step"] = "note"
            await update.message.reply_text("Note aggiuntive? (opzionale, scrivi - per saltare)")
        except ValueError:
            await update.message.reply_text("Inserisci un numero tra 1 e 10:")
        return

    # Step finale: note
    if step == "note":
        state["data"]["note"] = None if text.strip() == "-" else text.strip()

        await update.message.reply_text("Elaboro le metriche con AI...")

        # Recupera peso attuale
        peso_recente = get_recent_metrics(days=30)
        peso_kg = next((x["peso_kg"] for x in peso_recente if x.get("peso_kg")), 75.0)

        # Stima metriche con Claude
        metriche = stima_metriche_basket(state["data"], peso_kg)

        # Prepara record da salvare
        record = {
            "data_ora": datetime.now().isoformat(),
            "tipo": state["data"].get("tipo"),
            "minuti_giocati": state["data"].get("minuti_giocati"),
            "risultato": state["data"].get("risultato"),
            "durata_min": state["data"].get("durata_min"),
            "focus": state["data"].get("focus"),
            "intensita": state["data"].get("intensita"),
            "note": state["data"].get("note"),
            "calorie_stimate": metriche.get("calorie_stimate"),
            "fc_media_stimata": metriche.get("fc_media_stimata"),
            "fc_max_stimata": metriche.get("fc_max_stimata"),
            "distanza_stimata_km": metriche.get("distanza_stimata_km"),
            "carico_allenamento": metriche.get("carico_allenamento"),
        }

        try:
            supabase.table("basket_sessions").insert(record).execute()
            del basket_state[user_id]

            tipo = state["data"].get("tipo", "")
            if tipo == "partita":
                riepilogo = (
                    f"Partita salvata!\n\n"
                    f"Minuti giocati: {state['data'].get('minuti_giocati')}\n"
                    f"Risultato: {state['data'].get('risultato')}\n"
                    f"Intensita: {state['data'].get('intensita')}/10\n\n"
                    f"Stime AI:\n"
                    f"  Calorie: ~{metriche.get('calorie_stimate', 'N/D')} kcal\n"
                    f"  FC media: ~{metriche.get('fc_media_stimata', 'N/D')} bpm\n"
                    f"  FC max: ~{metriche.get('fc_max_stimata', 'N/D')} bpm\n"
                    f"  Distanza: ~{metriche.get('distanza_stimata_km', 'N/D')} km\n"
                    f"  Carico: {metriche.get('carico_allenamento', 'N/D')}\n\n"
                    f"Note coach: {metriche.get('spiegazione', '')}"
                )
            else:
                riepilogo = (
                    f"Allenamento basket salvato!\n\n"
                    f"Durata: {state['data'].get('durata_min')} min\n"
                    f"Focus: {state['data'].get('focus')}\n"
                    f"Intensita: {state['data'].get('intensita')}/10\n\n"
                    f"Stime AI:\n"
                    f"  Calorie: ~{metriche.get('calorie_stimate', 'N/D')} kcal\n"
                    f"  FC media: ~{metriche.get('fc_media_stimata', 'N/D')} bpm\n"
                    f"  FC max: ~{metriche.get('fc_max_stimata', 'N/D')} bpm\n"
                    f"  Carico: {metriche.get('carico_allenamento', 'N/D')}\n\n"
                    f"Note coach: {metriche.get('spiegazione', '')}"
                )
            await update.message.reply_text(riepilogo)

        except Exception as e:
            del basket_state[user_id]
            logger.error(f"Errore salvataggio basket: {e}")
            await update.message.reply_text("Errore nel salvataggio. Riprova con /basket.")

# ─────────────────────────────────────────────
# Gestione peso
# ─────────────────────────────────────────────

async def handle_peso_input(update, context, text):
    user_id = update.effective_user.id
    state = peso_state[user_id]
    campi = state["campi"]
    idx = state["idx"]
    if text.lower() in ("annulla", "cancel"):
        del peso_state[user_id]
        await update.message.reply_text("Inserimento annullato.")
        return
    nome, label, tipo = campi[idx]
    if text.strip() == "-":
        state["valori"][nome] = None
    else:
        try:
            state["valori"][nome] = tipo(text.strip().replace(",","."))
        except ValueError:
            await update.message.reply_text("Valore non valido. Inserisci un numero oppure - per saltare.")
            return
    state["idx"] += 1
    if state["idx"] < len(campi):
        _, label_next, _ = campi[state["idx"]]
        await update.message.reply_text(f"{label_next} (oppure - per saltare):")
    else:
        oggi = datetime.now().strftime("%Y-%m-%d")
        valori = {k:v for k,v in state["valori"].items() if v is not None}
        try:
            res = supabase.table("daily_health_metrics").select("id").eq("date", oggi).execute()
            if res.data:
                supabase.table("daily_health_metrics").update(valori).eq("date", oggi).execute()
            else:
                valori["date"] = oggi
                supabase.table("daily_health_metrics").insert(valori).execute()
            del peso_state[user_id]
            righe = "\n".join([f"  {label}: {state['valori'][nome]}" for nome,label,_ in campi if state['valori'].get(nome) is not None])
            await update.message.reply_text(f"Dati salvati per {oggi}:\n{righe}")
        except Exception as e:
            del peso_state[user_id]
            logger.error(f"Errore salvataggio peso: {e}")
            await update.message.reply_text("Errore nel salvataggio. Riprova con /peso.")

# ─────────────────────────────────────────────
# Handlers Telegram
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global n_messaggi_sessione
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato.")
        return
    user_message = update.message.text
    if user_id in peso_state:
        await handle_peso_input(update, context, user_message)
        return
    if user_id in basket_state:
        await handle_basket_input(update, context, user_message)
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        memoria = carica_memoria()
        ctx = build_context_for_claude(user_message, memoria)
        conversation_history.append({"role":"user","content":ctx})
        response = anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=conversation_history[-10:]
        )
        reply = response.content[0].text
        salva_token_usage(response.usage.input_tokens, response.usage.output_tokens, user_message)
        conversation_history.append({"role":"assistant","content":reply})
        n_messaggi_sessione += 1
        await update.message.reply_text(reply)
        if n_messaggi_sessione % 5 == 0:
            riassunto = genera_riassunto(conversation_history[-10:])
            if riassunto:
                salva_memoria(riassunto, n_messaggi_sessione)
    except Exception as e:
        logger.error(f"Errore: {e}")
        await update.message.reply_text("Si e verificato un errore. Riprova.")

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global n_messaggi_sessione
    conversation_history.clear()
    n_messaggi_sessione = 0
    memoria = carica_memoria()
    benvenuto = (
        "Ciao! Sono il tuo Coach Pro Fitness.\n\n"
        "Comandi:\n"
        "/start - ricomincia\n"
        "/reset - resetta conversazione\n"
        "/costi - costi token\n"
        "/peso - inserisci metriche Renpho\n"
        "/memoria - vedi ultimo riassunto\n"
        "/basket - registra sessione basket\n\n"
        "Iniziamo!"
    )
    if memoria:
        benvenuto += f"\n\nRicordo dalla nostra ultima sessione:\n{memoria[:200]}..."
    await update.message.reply_text(benvenuto)

async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global n_messaggi_sessione
    if conversation_history:
        riassunto = genera_riassunto(conversation_history)
        if riassunto:
            salva_memoria(riassunto, n_messaggi_sessione)
    conversation_history.clear()
    n_messaggi_sessione = 0
    await update.message.reply_text("Conversazione resettata! Ho salvato un riassunto della sessione.")

async def handle_costi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s30 = get_stats_token(30)
    s7  = get_stats_token(7)
    if not s30:
        await update.message.reply_text("Nessun dato disponibile.")
        return
    await update.message.reply_text(
        f"COSTI TOKEN\n\n"
        f"7 giorni: {s7.get('n_messaggi',0)} msg, {s7.get('tot_token',0):,} token, ${s7.get('tot_costo_usd',0):.4f}\n"
        f"30 giorni: {s30.get('n_messaggi',0)} msg, {s30.get('tot_token',0):,} token, ${s30.get('tot_costo_usd',0):.4f}\n"
        f"Mese corrente: ${s30.get('costo_mese_usd',0):.4f}\n"
        f"Media/msg: ${s30.get('costo_medio',0):.4f}"
    )

async def handle_memoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memoria = carica_memoria()
    if memoria:
        await update.message.reply_text(f"Ultimo riassunto:\n\n{memoria}")
    else:
        await update.message.reply_text("Nessun riassunto disponibile ancora.")

async def handle_peso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato.")
        return
    campi = [
        ("grasso_viscerale",        "Grasso viscerale (1-20)",    float),
        ("grasso_sottocutaneo_pct", "Grasso sottocutaneo (%)",    float),
        ("muscolo_scheletrico_pct", "Muscolo scheletrico (%)",    float),
        ("proteina_pct",            "Proteina (%)",               float),
        ("eta_metabolica",          "Eta metabolica (anni)",      int),
        ("massa_ossea_kg",          "Massa ossea (kg)",           float),
        ("acqua_corporea_pct",      "Acqua corporea (%)",         float),
        ("bmr_kj",                  "BMR (kJ)",                   float),
    ]
    peso_state[user_id] = {"campi":campi,"idx":0,"valori":{nome:None for nome,_,_ in campi}}
    await update.message.reply_text("Inserimento metriche Renpho\nScrivi - per saltare, annulla per uscire.\n\nGrasso viscerale (1-20):")

async def handle_basket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato.")
        return
    basket_state[user_id] = {"step": "tipo", "data": {}}
    await update.message.reply_text(
        "Registrazione sessione basket\n"
        "Scrivi annulla per uscire.\n\n"
        "Tipo di sessione?\n"
        "1 - Partita\n"
        "2 - Allenamento"
    )

# ─────────────────────────────────────────────
# Avvio
# ─────────────────────────────────────────────

def main():
    logger.info("Avvio Fitness Coach Agent...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   handle_start))
    app.add_handler(CommandHandler("reset",   handle_reset))
    app.add_handler(CommandHandler("costi",   handle_costi))
    app.add_handler(CommandHandler("peso",    handle_peso))
    app.add_handler(CommandHandler("memoria", handle_memoria))
    app.add_handler(CommandHandler("basket",  handle_basket))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot in ascolto...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
