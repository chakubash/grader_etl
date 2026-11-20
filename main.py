import ast
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import gspread
import psycopg2
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
API_URL = "https://api.itresume.ru/statistics/"


def setup_logging():
    log_folder = BASE_DIR / "logs"
    log_folder.mkdir(exist_ok=True)
    today = datetime.now().date()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_folder / f"{today}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )

    for file in log_folder.glob("*.log"):
        try:
            file_date = datetime.strptime(file.stem, "%Y-%m-%d").date()
            if file_date < today - timedelta(days=2):
                file.unlink()
        except ValueError:
            pass


def get_data():
    params = {
        "client": os.getenv("API_CLIENT", "Skillfactory"),
        "client_key": os.getenv("API_CLIENT_KEY", ""),
        "start": os.getenv("START_UTC", ""),
        "end": os.getenv("END_UTC", ""),
    }
    if not params["client_key"]:
        raise ValueError("Не указан API_CLIENT_KEY")

    start_time = time.time()
    logging.info("Начинаю скачивание данных за период %s — %s", params["start"], params["end"])
    response = requests.get(API_URL, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("API вернул не список")
    logging.info("Скачано %d записей за %.2f сек", len(data), time.time() - start_time)
    return data


def prepare_record(record):
    if not isinstance(record, dict):
        raise ValueError("Запись должна быть словарём")

    user_id = record.get("lti_user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Неверный lti_user_id")

    params = record.get("passback_params")
    if isinstance(params, str):
        if len(params) > 16000:
            raise ValueError("Слишком длинный passback_params")
        try:
            params = ast.literal_eval(params)
        except (ValueError, SyntaxError, TypeError, RecursionError):
            raise ValueError("Не удалось прочитать passback_params") from None
    elif params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("Неверный passback_params")

    oauth = params.get("oauth_consumer_key")
    source_id = params.get("lis_result_sourcedid")
    service_url = params.get("lis_outcome_service_url")
    for value in (oauth, source_id, service_url):
        if value is not None and not isinstance(value, str):
            raise ValueError("Неверный тип поля в passback_params")

    attempt_type = record.get("attempt_type")
    is_correct = record.get("is_correct")
    if attempt_type not in ("run", "submit"):
        raise ValueError("Неверный attempt_type")

    if type(is_correct) is int and is_correct in (0, 1):
        is_correct = bool(is_correct)
    elif isinstance(is_correct, str):
        if is_correct.lower() in ("1", "true"):
            is_correct = True
        elif is_correct.lower() in ("0", "false"):
            is_correct = False
        else:
            raise ValueError("Неверное значение is_correct")
    elif is_correct is not None and type(is_correct) is not bool:
        raise ValueError("Неверное значение is_correct")

    if attempt_type == "run" and is_correct is not None:
        raise ValueError("У run поле is_correct должно быть пустым")

    date_text = record.get("created_at")
    if not isinstance(date_text, str):
        raise ValueError("Неверная дата")
    try:
        created_at = datetime.fromisoformat(date_text)
    except ValueError:
        raise ValueError("Неверная дата") from None

    return (user_id, oauth, source_id, service_url,
            is_correct, attempt_type, created_at)


def prepare_data(data):
    logging.info("Начинаю обработку записей")
    rows = []
    for number, record in enumerate(data, 1):
        try:
            rows.append(prepare_record(record))
        except (ValueError, TypeError) as error:
            logging.warning("Пропущена запись %d: %s", number, error)
    logging.info("Обработано: %d, пропущено: %d", len(rows), len(data) - len(rows))
    return rows


def connect_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "54333"),
        dbname=os.getenv("DB_NAME", "grader_db"),
        user=os.getenv("DB_USER", "grader_user"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def save_data(rows):
    logging.info("Подключаюсь к PostgreSQL")
    connection = connect_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS grader_attempts (
                    user_id TEXT,
                    oauth_consumer_key TEXT,
                    lis_result_sourcedid TEXT,
                    lis_outcome_service_url TEXT,
                    is_correct BOOLEAN,
                    attempt_type TEXT,
                    created_at TIMESTAMP
                )
            """)
            logging.info("Начинаю загрузку в базу")
            cursor.executemany("""
                INSERT INTO grader_attempts
                (user_id, oauth_consumer_key, lis_result_sourcedid,
                 lis_outcome_service_url, is_correct, attempt_type, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, rows)
        connection.commit()
        logging.info("В PostgreSQL добавлено %d записей", len(rows))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def daily_report():
    connection = connect_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT created_at::date,
                       COUNT(*),
                       COUNT(*) FILTER (WHERE attempt_type = 'run'),
                       COUNT(*) FILTER (WHERE attempt_type = 'submit'),
                       COUNT(*) FILTER (WHERE attempt_type = 'submit' AND is_correct = TRUE),
                       COUNT(*) FILTER (WHERE attempt_type = 'submit' AND is_correct = FALSE),
                       COUNT(DISTINCT user_id)
                FROM grader_attempts
                GROUP BY created_at::date
                ORDER BY created_at::date
            """)
            data = cursor.fetchall()
    finally:
        connection.close()

    rows = []
    for day, total, runs, submits, correct, incorrect, users in data:
        rows.append([
            str(day), total, runs, submits, correct, incorrect, users,
            round(correct * 100 / total, 2) if total else 0,
            round(correct * 100 / submits, 2) if submits else 0,
        ])
    return rows


def upload_to_sheets():
    logging.info("Считаю статистику по дням")
    rows = daily_report()
    if not rows:
        logging.warning("В базе нет данных для Google Sheets")
        return

    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    if not sheet_id or sheet_id == "PASTE_SHEET_ID_HERE":
        raise ValueError("Укажите GOOGLE_SHEET_ID в .env")

    client = gspread.service_account(filename=str(BASE_DIR / "google_credentials.json"))
    sheet = client.open_by_key(sheet_id).sheet1
    headers = [
        "Дата", "Всего попыток", "Запусков", "Отправок",
        "Успешных", "Неуспешных", "Уникальных пользователей",
        "Успешных от всех, %", "Успешных от отправок, %",
    ]
    sheet.clear()
    sheet.update([headers] + rows, "A1", value_input_option="RAW")
    logging.info("Статистика за %d дней записана в Google Sheets", len(rows))


def main():
    load_dotenv(BASE_DIR / ".env")
    setup_logging()
    logging.info("Скрипт запущен")
    start_time = time.time()

    try:
        if "--sheets-only" in sys.argv:
            upload_to_sheets()
        else:
            if "--demo" in sys.argv:
                logging.info("Тестовый запуск без API")
                data = [
                    {"lti_user_id": "demo_user_001", "passback_params": "{'oauth_consumer_key': '', 'lis_result_sourcedid': 'demo', 'lis_outcome_service_url': None}",
                     "is_correct": None, "attempt_type": "run", "created_at": "2023-05-31 09:16:11"},
                    {"lti_user_id": "demo_user_001", "passback_params": "{'oauth_consumer_key': '', 'lis_result_sourcedid': 'demo', 'lis_outcome_service_url': None}",
                     "is_correct": True, "attempt_type": "submit", "created_at": "2023-05-31 09:18:12"},
                ]
            else:
                data = get_data()

            rows = prepare_data(data)
            save_data(rows)
            if "--demo" not in sys.argv:
                upload_to_sheets()

        logging.info("Готово. Общее время: %.2f сек", time.time() - start_time)
    except requests.exceptions.HTTPError as error:
        code = error.response.status_code if error.response is not None else "?"
        logging.error("Ошибка API, код ответа: %s", code)
        sys.exit(1)
    except requests.exceptions.RequestException as error:
        logging.error("Ошибка подключения к API: %s", type(error).__name__)
        sys.exit(1)
    except Exception as error:
        logging.exception("Ошибка выполнения: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
