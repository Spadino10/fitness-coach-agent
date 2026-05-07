"""
🏋️ Fitness Coach Agent
Telegram bot che usa Claude per analizzare i tuoi dati fitness da Supabase
"""

import os
import logging
from datetime import datetime, timedelta
from supabase import create_client
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ─────────────────────────────────────────────
# Configurazione — tutte le variabili vengono
# lette dalle environment variables di Railway
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
SUPABASE_URL     = os.environ["SUPABASE_URL"]
SUPABASE_KEY     = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID  = int(os.environ.get("ALLOWED_USER_ID", "0"))  # sicurezza: solo tu puoi usarlo

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# Inizializza client
supabase  = create_client(SUPABASE_URL, SUPABASE_KEY)
anthropic = Anthropic(api_key=ANTHROPIC_KEY)

# ─────────────────────────────────────────────
# Persona del coach
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """Sei un coach fitness personale professionale e preciso.
Il tuo nome è Coach Pro. Hai accesso ai dati reali dell'utente (Simone) registrati dal suo Apple Watch e dall'app Salute di iPhone.

Il tuo stile:
- Professionale, diretto e basato sui dati
- Fornisci analisi concrete con numeri reali dai suoi dati
- Evidenzia trend positivi e aree di miglioramento
- Suggerisci allenamenti specifici basati sullo storico
- Commenta qualità del sonno e recupero in relazione alle performance
- Dai consigli su obiettivi dinamici (es. se il peso sale suggerisci strategie, se il VO2max migliora commentalo)
- Obiettivo principale dell'utente: mantenersi in forma

Quando analizzi i dati:
1. Inizia sempre con un sommario dei dati più recenti
2. Identifica trend degli ultimi 7-30 giorni
3. Dai almeno un consiglio pratico e specifico
4. Usa emoji con moderazione per rendere il messaggio leggibile

Se l'utente fa domande generali sul fitness, rispondi comunque con precisione professionale.
Rispondi sempre in italiano."""


# ─────────────────────────────────────────────
# Funzioni per leggere i dati da Supabase
# ─────────────────────────────────────────────

def get_recent_metrics(days: int = 14) -> list:
    """Legge le metriche giornaliere degli ultimi N giorni."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = supabase.table("daily_health_metrics") \
        .select("*") \
        .gte("date", since) \
        .order("date", desc=True) \
        .execute()
    return result.data or []


def get_recent_workouts(days: int = 30) -> list:
    """Legge le sessioni di allenamento degli ultimi N giorni."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = supabase.table("workouts") \
        .select("*") \
        .gte("data_ora", since) \
        .order("data_ora", desc=True) \
        .execute()
    return result.data or []


def get_weight_trend(days: int = 90) -> list:
    """Legge il trend peso degli ultimi N giorni."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = supabase.table("daily_health_metrics") \
        .select("date, peso_kg") \
        .gte("date", since) \
        .not_.is_("peso_kg", "null") \
        .order("date", desc=True) \
        .execute()
    return result.data or []


def get_summary_stats() -> dict:
    """Calcola statistiche aggregate degli ultimi 30 giorni."""
    metrics = get_recent_metrics(days=30)
    if not metrics:
        return {}

    def avg(lst, key):
        vals = [x[key] for x in lst if x.get(key)]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "periodo_giorni": len(metrics),
        "passi_medi": avg(metrics, "passi"),
        "distanza_media_km": avg(metrics, "distanza_km"),
        "fc_riposo_media": avg(metrics, "freq_cardiaca_riposo"),
        "fc_media": avg(metrics, "freq_cardiaca_media"),
        "sonno_medio_hr": avg(metrics, "sonno_totale_hr"),
        "sonno_profondo_medio_hr": avg(metrics, "sonno_profondo_hr"),
        "sonno_rem_medio_hr": avg(metrics, "sonno_rem_hr"),
        "vo2max_recente": next((x["vo2max"] for x in metrics if x.get("vo2max")), None),
        "variabilita_fc_media": avg(metrics, "variabilita_fc_ms"),
        "energia_attiva_media_kj": avg(metrics, "energia_attiva_kj"),
        "tempo_esercizio_medio_min": avg(metrics, "tempo_esercizio_min"),
    }


def build_context_for_claude(user_message: str) -> str:
    """Costruisce il contesto dati da passare a Claude."""
    metrics_recenti = get_recent_metrics(days=14)
    workouts_recenti = get_recent_workouts(days=30)
    peso_trend = get_weight_trend(days=90)
    stats = get_summary_stats()

    # Formatta i dati più recenti (ultimi 7 giorni)
    ultimi_7 = metrics_recenti[:7]
    righe_metriche = []
    for m in ultimi_7:
        riga = f"  {m['date']}: passi={m.get('passi','?')}, peso={m.get('peso_kg','?')}kg, " \
               f"FC_riposo={m.get('freq_cardiaca_riposo','?')}bpm, " \
               f"sonno={m.get('sonno_totale_hr','?')}h (REM={m.get('sonno_rem_hr','?')}h), " \
               f"esercizio={m.get('tempo_esercizio_min','?')}min"
        righe_metriche.append(riga)

    # Trend peso
    pesi = [(x['date'], x['peso_kg']) for x in peso_trend if x.get('peso_kg')]
    peso_info = ""
    if len(pesi) >= 2:
        peso_recente = pesi[0][1]
        peso_vecchio = pesi[-1][1]
        delta = round(peso_recente - peso_vecchio, 1)
        segno = "+" if delta > 0 else ""
        peso_info = f"Peso: {peso_recente}kg ({segno}{delta}kg negli ultimi {len(pesi)} giorni con misurazioni)"

    # Allenamenti recenti
    workout_info = f"{len(workouts_recenti)} sessioni negli ultimi 30 giorni"
    if workouts_recenti:
        ultimo = workouts_recenti[0]
        workout_info += f" | Ultimo: {ultimo['data_ora'][:10]}, FC media {ultimo.get('fc_media','?')}bpm"

    context = f"""
=== DATI REALI DELL'UTENTE (aggiornati) ===

📊 STATISTICHE ULTIMI 30 GIORNI:
- Passi medi/giorno: {stats.get('passi_medi', 'N/D')}
- Distanza media: {stats.get('distanza_media_km', 'N/D')} km/giorno
- FC a riposo media: {stats.get('fc_riposo_media', 'N/D')} bpm
- Sonno medio: {stats.get('sonno_medio_hr', 'N/D')} h (Profondo: {stats.get('sonno_profondo_medio_hr', 'N/D')}h, REM: {stats.get('sonno_rem_medio_hr', 'N/D')}h)
- VO2 Max: {stats.get('vo2max_recente', 'N/D')} ml/kg/min
- HRV (variabilità FC): {stats.get('variabilita_fc_media', 'N/D')} ms
- Energia attiva media: {stats.get('energia_attiva_media_kj', 'N/D')} kJ
- Tempo esercizio medio: {stats.get('tempo_esercizio_medio_min', 'N/D')} min/giorno
- Allenamenti: {workout_info}

⚖️ PESO:
{peso_info if peso_info else 'Dati peso non disponibili nel periodo'}

📅 ULTIMI 7 GIORNI (dettaglio):
{chr(10).join(righe_metriche) if righe_metriche else 'Nessun dato recente'}

=== MESSAGGIO DELL'UTENTE ===
{user_message}
"""
    return context


# ─────────────────────────────────────────────
# Gestione messaggi Telegram
# ─────────────────────────────────────────────

# Storico conversazione per sessione (in memoria)
conversation_history: list = []

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce ogni messaggio ricevuto su Telegram."""
    user_id = update.effective_user.id

    # Sicurezza: accetta solo messaggi dall'utente autorizzato
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo bot.")
        return

    user_message = update.message.text
    logger.info(f"Messaggio ricevuto: {user_message[:50]}...")

    # Mostra "sta scrivendo..."
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        # Costruisci il contesto con i dati reali
        context_with_data = build_context_for_claude(user_message)

        # Aggiungi alla conversazione
        conversation_history.append({
            "role": "user",
            "content": context_with_data
        })

        # Chiama Claude
        response = anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=conversation_history[-10:]  # ultime 10 messaggi per contesto
        )

        reply = response.content[0].text

        # Salva risposta nella storia
        conversation_history.append({
            "role": "assistant",
            "content": reply
        })

        # Invia risposta
        await update.message.reply_text(reply)
        logger.info("Risposta inviata con successo")

    except Exception as e:
        logger.error(f"Errore: {e}")
        await update.message.reply_text(
            "⚠️ Si è verificato un errore. Riprova tra qualche secondo."
        )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Risponde al comando /start."""
    conversation_history.clear()
    await update.message.reply_text(
        "👋 Ciao! Sono il tuo *Coach Pro Fitness*.\n\n"
        "Ho accesso ai tuoi dati di allenamento, peso, sonno e frequenza cardiaca.\n\n"
        "Puoi chiedermi:\n"
        "• Come sto andando questa settimana?\n"
        "• Analizza il mio sonno\n"
        "• Suggeriscimi un allenamento\n"
        "• Come è il mio trend peso?\n"
        "• Qual è il mio VO2 Max?\n\n"
        "Iniziamo! 💪",
        parse_mode="Markdown"
    )


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resetta la conversazione."""
    conversation_history.clear()
    await update.message.reply_text("🔄 Conversazione resettata. Possiamo ricominciare!")


# ─────────────────────────────────────────────
# Avvio del bot
# ─────────────────────────────────────────────

def main():
    logger.info("🚀 Avvio Fitness Coach Agent...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Registra handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Bot in ascolto su Telegram...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
