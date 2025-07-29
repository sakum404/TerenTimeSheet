import logging
import json
import os
import gspread
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Updater, CommandHandler, MessageHandler,
    CallbackContext, Filters, ConversationHandler
)
from threading import Thread
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from collections import OrderedDict

# --- Настройки из переменных окружения ---
try:
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    ALLOWED_PASSWORD = os.environ["BOT_PASSWORD"]
    FORM_URL = os.environ["FORM_URL"]
    GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
except KeyError as e:
    raise RuntimeError(f"Не задана переменная окружения: {e}")

# --- Google Sheets ---
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
SPREADSHEET_NAME = "TerenTimeSheets"
PROJECTS_SHEET = "projects_sheet"
LOG_SHEET = "WebAppData"

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Авторизация Google API ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
    client = gspread.authorize(creds)
    project_sheet = client.open(SPREADSHEET_NAME).worksheet(PROJECTS_SHEET)
    log_sheet = client.open(SPREADSHEET_NAME).worksheet(LOG_SHEET)
except Exception as e:
    logger.error(f"Ошибка подключения к Google Sheets: {e}")
    raise

# --- FastAPI ---
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Или укажи только https://terentimesheet.utc-service.kz
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/ping")
async def ping():
    return PlainTextResponse("pong")

@app.get("/form.html")
async def serve_form():
    return FileResponse("static/form.html")

@app.get("/form-data")
async def serve_form_data():
    try:
        data = project_sheet.get_all_records()
    except Exception as e:
        logger.exception("Ошибка получения данных из Google Sheets")
        return JSONResponse(content={"error": "Ошибка получения данных"}, status_code=500)

    fields_data = {
        "projects": sorted(set(row["projects"] for row in data if row.get("projects"))),
        "period": list(OrderedDict.fromkeys(row["period"] for row in data if row.get("period"))),
        "executor": sorted(set(row["executor"] for row in data if row.get("executor"))),
        "task": sorted(set(row["task"] for row in data if row.get("task"))),
        "time_frame": sorted(set(row["time_frame"] for row in data if row.get("time_frame"))),
        "difficulty_level": sorted(set(row["difficulty_level"] for row in data if row.get("difficulty_level"))),
    }

    position_map = {}
    for row in data:
        name = row.get("executor", "")
        pos = row.get("position", "")
        if name and pos:
            position_map[name] = pos

    return JSONResponse({**fields_data, "position_map": position_map})


# --- Telegram bot ---
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

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ASK_PASSWORD: [MessageHandler(Filters.text & ~Filters.command, check_password)]},
        fallbacks=[]
    )
    dp.add_handler(conv_handler)
    dp.add_handler(MessageHandler(Filters.status_update.web_app_data, receive_webapp))
    updater.start_polling()
    updater.idle()


# --- Запуск в отдельном потоке ---
if __name__ == "__main__":
    Thread(target=run_telegram, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
