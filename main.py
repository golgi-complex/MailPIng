import email
from email.header import decode_header
import html
import imaplib
import logging
import os
import time
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import requests

# Загружаем переменные из .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Считываем конфигурацию
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

GMAIL_USER_1 = os.getenv("GMAIL_USER_1")
GMAIL_PASSWORD_1 = os.getenv("GMAIL_PASSWORD_1")

GMAIL_USER_2 = os.getenv("GMAIL_USER_2")
GMAIL_PASSWORD_2 = os.getenv("GMAIL_PASSWORD_2")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 30))
SENDER_FILTER = os.getenv("SENDER_FILTER", "noreply@kufar.by")


def send_telegram_message(text: str):
    """Отправляет текстовое сообщение в Telegram."""
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("BOT_TOKEN или CHAT_ID не заполнены в .env!")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("Сообщение успешно отправлено в Telegram")
        else:
            # Если Telegram отклонил HTML (например, из-за непарного тега), шлем чистым текстом
            logger.warning(f"Ошибка HTML разметки ({response.status_code}). Пробуем без HTML.")
            clean_payload_text = BeautifulSoup(text, "html.parser").get_text()
            payload["text"] = clean_payload_text
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=10)

    except Exception as e:
        logger.exception(f"Не удалось отправить сообщение в Telegram: {e}")


def decode_str(header_value: str) -> str:
    """Декодирует MIME-заголовки писем."""
    if not header_value:
        return ""
    decoded_fragments = decode_header(header_value)
    result = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            result.append(fragment.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(str(fragment))
    return "".join(result)


def extract_clean_text(msg: email.message.Message) -> str:
    """Извлекает и очищает текст письма от HTML, скриптов и стилевых блоков."""
    html_content = ""
    text_content = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if "attachment" in content_disposition:
                continue

            if content_type == "text/html" and not html_content:
                payload = part.get_payload(decode=True)
                if payload:
                    html_content = payload.decode("utf-8", errors="replace")
            elif content_type == "text/plain" and not text_content:
                payload = part.get_payload(decode=True)
                if payload:
                    text_content = payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text_content = payload.decode("utf-8", errors="replace")

    # Если есть HTML-версия письма, чистим её BeautifulSoup
    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")

        # Удаляем служебные/скрытые теги
        for element in soup(["script", "style", "head", "title", "meta", "[document]"]):
            element.decompose()

        clean_html_text = soup.get_text(separator="\n", strip=True)
        if clean_html_text:
            text_content = clean_html_text

    elif text_content:
        soup = BeautifulSoup(text_content, "html.parser")
        text_content = soup.get_text(separator="\n", strip=True)

    # Убираем пустые строки
    lines = [line.strip() for line in text_content.splitlines() if line.strip()]
    return "\n".join(lines)


def check_account(email_user: str, email_pass: str, sender_filter: str):
    """Проверяет почтовый ящик на новые письма."""
    if not email_user or not email_pass:
        return

    logger.info(f"Проверка ящика: {email_user}")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_user, email_pass)
        mail.select("INBOX")

        status, search_data = mail.search(None, "UNSEEN")
        if status != "OK":
            logger.error(f"Ошибка поиска писем в {email_user}")
            return

        msg_nums = search_data[0].split()
        if not msg_nums:
            logger.info(f"Нет новых писем в {email_user}")
            mail.logout()
            return

        logger.info(f"Найдено {len(msg_nums)} новых писем в {email_user}")

        for num in msg_nums:
            status, fetch_data = mail.fetch(num, "(RFC822)")
            if status != "OK" or not fetch_data:
                continue

            raw_email_bytes = None
            for response_part in fetch_data:
                if isinstance(response_part, tuple):
                    body_data = response_part[1]
                    if isinstance(body_data, (bytes, bytearray)):
                        raw_email_bytes = bytes(body_data)
                    break

            if not raw_email_bytes:
                continue

            msg = email.message_from_bytes(raw_email_bytes)
            sender = decode_str(msg.get("From", ""))

            if sender_filter and sender_filter.lower() not in sender.lower():
                continue

            clean_text = extract_clean_text(msg)

            safe_email = html.escape(email_user)
            safe_text = html.escape(clean_text)

            telegram_text = (
                f"📬 <b>Получатель:</b> <code>{safe_email}</code>\n\n"
                f"<b>Текст письма:</b>\n{safe_text}"
            )

            if len(telegram_text) > 4000:
                telegram_text = telegram_text[:3990] + "\n\n<i>[Текст обрезан...]</i>"

            send_telegram_message(telegram_text)

        mail.logout()

    except imaplib.IMAP4.error as e:
        logger.error(f"Ошибка авторизации IMAP для {email_user}: {e}")
    except Exception as e:
        logger.exception(f"Непредвиденная ошибка при проверке {email_user}: {e}")


def main():
    # Проверка наличия .env данных перед запуском
    if not BOT_TOKEN or not CHAT_ID:
        logger.critical("Ошибка: BOT_TOKEN или CHAT_ID не найдены в .env файле!")
        return

    accounts = []
    if GMAIL_USER_1 and GMAIL_PASSWORD_1:
        accounts.append((GMAIL_USER_1, GMAIL_PASSWORD_1))
    if GMAIL_USER_2 and GMAIL_PASSWORD_2:
        accounts.append((GMAIL_USER_2, GMAIL_PASSWORD_2))

    if not accounts:
        logger.critical("Ошибка: Не указано ни одной пары логин/пароль Gmail в .env!")
        return

    logger.info(f"Бот запущен. Проверка каждые {CHECK_INTERVAL} сек. Фильтр: {SENDER_FILTER}")

    while True:
        for email_user, email_pass in accounts:
            check_account(email_user, email_pass, SENDER_FILTER)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
