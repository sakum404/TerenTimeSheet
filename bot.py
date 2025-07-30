import logging
import json
import gspread
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from threading import Thread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, CallbackContext,
    Filters, ConversationHandler
)

# === Конфигурация ===
TELEGRAM_TOKEN = "7523210458:AAFsp53vLRkriA3rjYewxcrKD27wjQ_ahnw"
ALLOWED_PASSWORD = "Teren2024"
FORM_URL = "https://terentimesheet.utc-service.kz/form.html"  # Укажи HTTPS ссылку

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS_FILE = "credentials.json"
SPREADSHEET_NAME = "TerenTimeSheets"
PROJECTS_SHEET = "projects_sheet"
LOG_SHEET = "WebAppData"

# === Авторизация Google Sheets ===
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
client = gspread.authorize(creds)
project_sheet = client.open(SPREADSHEET_NAME).worksheet(PROJECTS_SHEET)
log_sheet = client.open(SPREADSHEET_NAME).worksheet(LOG_SHEET)

# === FastAPI ===
app = FastAPI()

@app.get("/form.html")
def get_form():
    return FileResponse("form.html")

@app.get("/form-data")
def get_form_data():
    data = project_sheet.get_all_records()
    fields_data = {
        "projects": sorted(set(row["projects"] for row in data if row["projects"])),
        "period": sorted(set(row["period"] for row in data if row["period"])),
        "executor": sorted(set(row["executor"] for row in data if row["executor"])),
        "task": sorted(set(row["task"] for row in data if row["task"])),
        "time_frame": sorted(set(row["time_frame"] for row in data if row["time_frame"])),
        "difficulty_level": sorted(set(row["difficulty_level"] for row in data if row["difficulty_level"])),
    }
    position_map = {
        row["executor"]: row["position"]
        for row in data if row.get("executor") and row.get("position")
    }
    return JSONResponse({**fields_data, "position_map": position_map})

# === Telegram Bot ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ASK_PASSWORD = 1
authorized_users = set()

def start(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    if user_id in authorized_users:
        return send_webapp_button(update)
    update.message.reply_text("🔒 Введите пароль:")
    return ASK_PASSWORD

def check_password(update: Update, context: CallbackContext) -> int:
    if update.message.text.strip() == ALLOWED_PASSWORD:
        authorized_users.add(update.message.from_user.id)
        update.message.reply_text("✅ Доступ разрешён.")
        return send_webapp_button(update)
    update.message.reply_text("❌ Неверный пароль. Попробуйте снова.")
    return ASK_PASSWORD

def send_webapp_button(update: Update) -> int:
    button = [[KeyboardButton("📝 Заполнить", web_app=WebAppInfo(url=FORM_URL))]]
    markup = ReplyKeyboardMarkup(button, resize_keyboard=True)
    update.message.reply_text("Нажмите, чтобы заполнить форму:", reply_markup=markup)
    return ConversationHandler.END

def receive_webapp(update: Update, context: CallbackContext):
    if update.message.web_app_data:
        try:
            data = json.loads(update.message.web_app_data.data)
            user = update.message.from_user.username or update.message.from_user.full_name
            log_sheet.append_row([
                user,
                data.get("projects", ""),
                data.get("period", ""),
                data.get("executor", ""),
                data.get("position", ""),
                data.get("task", ""),
                data.get("time_frame", ""),
                data.get("difficulty_level", ""),
                data.get("time", ""),
                data.get("overtime", ""),
                data.get("comment", "")
            ])
            update.message.reply_text("✅ Сохранено.")
        except Exception as e:
            logger.exception("Ошибка при записи данных")
            update.message.reply_text("⚠️ Ошибка при обработке данных.")
    else:
        update.message.reply_text("⚠️ Нет данных из WebApp.")

def run_telegram():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ASK_PASSWORD: [MessageHandler(Filters.text & ~Filters.command, check_password)]},
        fallbacks=[]
    ))
    dp.add_handler(MessageHandler(Filters.status_update.web_app_data, receive_webapp))
    updater.start_polling()
    updater.idle()

def run_fastapi():
    import uvicorn
    uvicorn.run(app, host="localhost", port=5000)

if __name__ == "__main__":
    Thread(target=run_fastapi, daemon=True).start()
    run_telegram()
