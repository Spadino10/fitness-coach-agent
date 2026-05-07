"""
🏋️ Fitness Coach Agent
Telegram bot che usa Claude per analizzare i tuoi dati fitness da Supabase
Con tracciamento interno dei token e costi
"""

import os
import logging
from datetime import datetime, timedelta
from supabase import create_client
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ─────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
SUPABASE_URL     = os.environ["SUPABASE_URL"]
SUPABASE_KEY     = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID  = int(os.environ.get("ALLOWED_USER_ID", "0"))

# Prezzi claude-sonnet-4-5 per milione di token (USD)
PRICE_INPUT_PER_M  = 3.00
PRICE_OUTPUT_PER_M = 15.00

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
# Funzioni Supabase — dati fitness
# ─────────────────────────────────────────────

def get_recent_metrics(days: int = 14) -> list:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = supabase.table("daily_health_metrics") \
        .select("*") \
        .gte("date", since) \
        .order("date", desc=True) \
        .execute()
    return result.data or []


def get_recent_workouts(days: int = 30) -> list:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = supabase.table("workouts") \
        .select("*") \
        .gte("data_ora", since) \
        .order("data_ora", desc=True) \
        .execute()
    return result.data or []


def get_weight_trend(days: int = 90) -> list:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = supabase.table("daily_health_metrics") \
        .select("date, peso_kg") \
        .gte("date", since) \
        .not_.is_("peso_kg", "null") \
        .order("date", desc=True) \
        .execute()
    return result.data or []


def get_summary_stats() -> dict:
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
    metrics_recenti = get_recent_metrics(days=14)
    workouts_recenti = get_recent_workouts(days=30)
    peso_trend = get_weight_trend(days=90)
    stats = get_summary_stats()

    ultimi_7 = metrics_recenti[:7]
    righe_metriche = []
    for m in ultimi_7:
        riga = f"  {m['date']}: passi={m.get('passi','?')}, peso={m.get('peso_kg','?')}kg, " \
               f"FC_riposo={m.get('freq_cardiaca_riposo','?')}bpm, " \
               f"sonno={m.get('sonno_totale_hr','?')}h (REM={m.get('sonno_rem_hr','?')}h), " \
               f"esercizio={m.get('tempo_esercizio_min','?')}min"
        righe_metriche.append(riga)

    pesi = [(x['date'], x['peso_kg']) for x in peso_trend if x.get('peso_kg')]
    peso_info = ""
    if len(pesi) >= 2:
        peso_recente = pesi[0][1]
        peso_vecchio = pesi[-1][1]
        delta = round(peso_recente - peso_vecchio, 1)
        segno = "+" if delta > 0 else ""
        peso_info = f"Peso: {peso_recente}kg ({segno}{delta}kg negli ultimi {len(pesi)} giorni con misurazioni)"

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
# Funzioni tracciamento token
# ─────────────────────────────────────────────

def calcola_costo(input_tokens: int, output_tokens: int) -> float:
    """Calcola il costo in USD basato sui token usati."""
    costo_input  = (input_tokens  / 1_000_000) * PRICE_INPUT_PER_M
    costo_output = (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_M
    return round(costo_input + costo_output, 6)


def salva_token_usage(input_tokens: int, output_tokens: int, messaggio: str):
    """Salva il consumo token su Supabase."""
    costo = calcola_costo(input_tokens, output_tokens)
    try:
        supabase.table("token_usage").insert({
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "costo_usd":     costo,
            "messaggio":     messaggio[:100]  # primi 100 char del messaggio
        }).execute()
    except Exception as e:
        logger.error(f"Errore salvataggio token: {e}")


def get_stats_token(giorni: int = 30) -> dict:
    """Legge le statistiche token degli ultimi N giorni."""
    since = (datetime.now() - timedelta(days=giorni)).isoformat()
    try:
        result = supabase.table("token_usage") \
            .select("*") \
            .gte("timestamp", since) \
            .execute()
        rows = result.data or []

        if not rows:
            return {}

        tot_input    = sum(r["input_tokens"]  for r in rows)
        tot_output   = sum(r["output_tokens"] for r in rows)
        tot_costo    = sum(r["costo_usd"]     for r in rows)
        n_messaggi   = len(rows)

        # Stats del mese corrente
        inizio_mese = datetime.now().replace(day=1, hour=0, minute=0, second=0).isoformat()
        rows_mese = [r for r in rows if r["timestamp"] >= inizio_mese]
        costo_mese = sum(r["costo_usd"] for r in rows_mese)

        return {
            "giorni":         giorni,
            "n_messaggi":     n_messaggi,
            "tot_input":      tot_input,
            "tot_output":     tot_output,
            "tot_token":      tot_input + tot_output,
            "tot_costo_usd":  round(tot_costo, 4),
            "costo_mese_usd": round(costo_mese, 4),
            "costo_medio":    round(tot_costo / n_messaggi, 4) if n_messaggi else 0,
        }
    except Exception as e:
        logger.error(f"Errore lettura token: {e}")
        return {}


# ─────────────────────────────────────────────
# Gestione messaggi Telegram
# ─────────────────────────────────────────────

conversation_history: list = []


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    user_message = update.message.text
    logger.info(f"Messaggio ricevuto: {user_message[:50]}...")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        context_with_data = build_context_for_claude(user_message)
        conversation_history.append({"role": "user", "content": context_with_data})

        response = anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=conversation_history[-10:]
        )

        reply = response.content[0].text

        # Salva token usage
        salva_token_usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            messaggio=user_message
        )
        logger.info(f"Token usati — input: {response.usage.input_tokens}, output: {response.usage.output_tokens}")

        conversation_history.append({"role": "assistant", "content": reply})
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Errore: {e}")
        await update.message.reply_text("⚠️ Si è verificato un errore. Riprova tra qualche secondo.")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "Comandi disponibili:\n"
        "/start — ricomincia\n"
        "/reset — resetta la conversazione\n"
        "/costi — mostra i costi token\n\n"
        "Iniziamo! 💪",
        parse_mode="Markdown"
    )


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversation_history.clear()
    await update.message.reply_text("🔄 Conversazione resettata!")


async def handle_costi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra le statistiche di utilizzo token e costi."""
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    stats_30  = get_stats_token(giorni=30)
    stats_7   = get_stats_token(giorni=7)

    if not stats_30:
        await update.message.reply_text("📊 Nessun dato di utilizzo disponibile ancora.")
        return

    msg = (
        "📊 *RIEPILOGO COSTI TOKEN*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Ultimi 7 giorni:*\n"
        f"  💬 Messaggi: {stats_7.get('n_messaggi', 0)}\n"
        f"  🔢 Token totali: {stats_7.get('tot_token', 0):,}\n"
        f"  💰 Costo: ${stats_7.get('tot_costo_usd', 0):.4f}\n\n"
        f"*Ultimi 30 giorni:*\n"
        f"  💬 Messaggi: {stats_30.get('n_messaggi', 0)}\n"
        f"  🔢 Token totali: {stats_30.get('tot_token', 0):,}\n"
        f"  📥 Input token: {stats_30.get('tot_input', 0):,}\n"
        f"  📤 Output token: {stats_30.get('tot_output', 0):,}\n"
        f"  💰 Costo totale: ${stats_30.get('tot_costo_usd', 0):.4f}\n"
        f"  📅 Costo mese corrente: ${stats_30.get('costo_mese_usd', 0):.4f}\n"
        f"  📈 Costo medio/msg: ${stats_30.get('costo_medio', 0):.4f}\n\n"
        f"_Prezzi: $3/M token input · $15/M token output_"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ─────────────────────────────────────────────
# Avvio
# ─────────────────────────────────────────────

def main():
    logger.info("🚀 Avvio Fitness Coach Agent...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  handle_start))
    app.add_handler(CommandHandler("reset",  handle_reset))
    app.add_handler(CommandHandler("costi",  handle_costi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Bot in ascolto su Telegram...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
