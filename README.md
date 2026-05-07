# 🏋️ Fitness Coach Agent

Bot Telegram che usa Claude AI per analizzare i tuoi dati fitness da Apple Watch / app Salute.

## Stack
- **Claude** (Anthropic) — intelligenza del coach
- **Supabase** — database dati fitness
- **python-telegram-bot** — interfaccia Telegram
- **Railway** — hosting del bot

## Variabili d'ambiente (da configurare su Railway)

| Variabile | Descrizione |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot da BotFather |
| `SUPABASE_URL` | URL del progetto Supabase |
| `SUPABASE_KEY` | anon public key di Supabase |
| `ANTHROPIC_API_KEY` | API key di Anthropic |
| `ALLOWED_USER_ID` | Il tuo Telegram user ID (sicurezza) |

## Come trovare il tuo Telegram User ID
Scrivi a @userinfobot su Telegram — ti risponde con il tuo ID numerico.

## Comandi disponibili
- `/start` — avvia il bot e mostra il menu
- `/reset` — resetta la conversazione

## Deploy su Railway
1. Fai push del codice su GitHub
2. Crea nuovo progetto su Railway collegato al repo
3. Aggiungi le variabili d'ambiente
4. Railway deploya automaticamente
