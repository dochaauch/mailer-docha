import pandas as pd
import os
import io
import json
import yagmail
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import shutil
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'clients_config.json')

def load_clients_config():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)

def get_google_sheet_data(sheet_id, sheet_name, credentials_path):
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    # Отключаем кеширование discovery, чтобы избежать предупреждений file_cache
    service = build(
        "sheets", "v4",
        credentials=credentials,
        cache_discovery=False
    )
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=sheet_name
    ).execute()
    values = result.get('values', [])
    if not values:
        return pd.DataFrame()
    headers = values[0]
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers)

def get_drive_service(credentials_path):
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    # Отключаем кеширование discovery
    return build(
        'drive', 'v3',
        credentials=credentials,
        cache_discovery=False
    )

def get_pdf_files_map(folder_id, service):
    results = service.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/pdf'",
        fields="files(id, name)"
    ).execute()
    return {file['name']: file['id'] for file in results['files']}

def download_pdf(file_id, local_path, service):
    request = service.files().get_media(fileId=file_id)
    file_io = io.BytesIO()
    downloader = MediaIoBaseDownload(file_io, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    with open(local_path, 'wb') as f:
        f.write(file_io.getvalue())

def send_email(user, password, to, subject, body, attachment, bcc=None):
    yag = yagmail.SMTP(user=user, password=password, timeout=30)
    yag.send(to=to, bcc=bcc, subject=subject, contents=body, attachments=attachment)

def send_control_email(client_config, result):
    subject = f"[ОТЧЕТ] Рассылка для {client_config['display_name']}"
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = f"""Рассылка завершена {time_now}.

Клиент: {client_config['login']}
Всего отправлено: {len(result['sent'])}
Ошибок: {len(result['skipped'])}

Ошибки:
"""
    for email, reason in result['skipped']:
        body += f"- {email}: {reason}\n"
    send_email(
        user=client_config['email_user'],
        password=client_config['email_password'],
        to=client_config['control_email'],
        subject=subject,
        body=body,
        attachment=None
    )

def preview_emails(client_config):
    credentials_path = os.path.join(BASE_DIR, client_config['credentials_path'])
    df = get_google_sheet_data(
        sheet_id=client_config['sheet_id'],
        sheet_name=client_config['sheet_name'],
        credentials_path=credentials_path
    )
    drive_service = get_drive_service(credentials_path)
    pdf_map = get_pdf_files_map(client_config['folder_id'], drive_service)

    ready = []
    skipped = []
    used_files = set()
    file_usage = {}

    for _, row in df.iterrows():
        apt_number = str(row['apt_number']).strip()
        email = str(row.get('email', '')).strip()
        kr_nr = str(row.get('kr_nr', '')).strip()

        if not email:
            skipped.append(("", f"Отсутствует email для квартиры {kr_nr}"))
            continue

        matched_file = next((fname for fname in pdf_map if fname.startswith(apt_number)), None)
        if not matched_file:
            skipped.append((email, f"Файл PDF не найден по шаблону apt_number ({apt_number})"))
            continue

        if matched_file in file_usage:
            skipped.append((email, f"Файл PDF {matched_file} уже сопоставлен с другой строкой"))
            continue

        file_usage[matched_file] = email
        used_files.add(matched_file)

        ready.append({
            "apt_number": apt_number,
            "kr_nr": kr_nr,
            "email": email,
            "pdf": matched_file
        })

    unused_pdfs = [pdf for pdf in pdf_map if pdf not in used_files]
    for fname in unused_pdfs:
        skipped.append(("-", f"Файл {fname} не был сопоставлен ни с одной строкой в таблице"))

    return {"ready": ready, "skipped": skipped}

def process_and_send_emails(client_config):
    if not client_config.get('active', False):
        return {'sent': [], 'skipped': [('ВСЕ', 'Клиент не активен — рассылка отключена')]}

    credentials_path = os.path.join(BASE_DIR, client_config['credentials_path'])
    df = get_google_sheet_data(
        sheet_id=client_config['sheet_id'],
        sheet_name=client_config['sheet_name'],
        credentials_path=credentials_path
    )
    drive_service = get_drive_service(credentials_path)
    pdf_map = get_pdf_files_map(client_config['folder_id'], drive_service)

    tmp_path = os.path.join(BASE_DIR, 'tmp_pdf', client_config['login'])
    os.makedirs(tmp_path, exist_ok=True)

    sent = []
    skipped = []
    file_usage = set()
    count = 0

    # Параметры пауз (можно задавать в clients_config.json)
    delay_between = client_config.get('delay_between_emails', 2)  # секунд
    pause_after = client_config.get('pause_after', 20)  # писем
    long_pause = client_config.get('long_pause', 60)  # секунд

    for _, row in df.iterrows():
        apt_number = str(row['apt_number']).strip()
        email = str(row.get('email', '')).strip()
        kr_nr = str(row.get('kr_nr', '')).strip()
        full_address = client_config.get("address_prefix", "") + kr_nr

        if not email:
            skipped.append(("", f"Отсутствует email для квартиры {kr_nr}"))
            continue

        matched_file = next((fname for fname in pdf_map if fname.startswith(apt_number)), None)
        if not matched_file:
            skipped.append((email, 'Файл PDF не найден по шаблону apt_number'))
            continue

        if matched_file in file_usage:
            skipped.append((email, f"Файл PDF {matched_file} уже использован в другой строке"))
            continue

        file_usage.add(matched_file)
        local_file = os.path.join(tmp_path, matched_file)

        try:
            # Повторно создаём SMTP-сессию каждые 10 писем, чтобы не препятствовать соединению
            if count % client_config.get('reconnect_every', 10) == 0:
                yag = yagmail.SMTP(
                    user=client_config['email_user'],
                    password=client_config['email_password'],
                    timeout=30
                )
            download_pdf(pdf_map[matched_file], local_file, drive_service)
            raw_body = client_config['email_body']
            custom_body = raw_body \
                .replace("{{kr_nr}}", kr_nr) \
                .replace("{{full_address}}", full_address)
            start_time = time.time()
            yag.send(
                to=email,
                bcc=client_config.get('email_bcc'),
                subject=client_config['email_subject'],
                contents=custom_body,
                attachments=local_file
            )
            duration = time.time() - start_time
            print(f"✅ Email sent to {email} in {duration:.2f} seconds")
            sent.append(email)
            count += 1

            # Небольшая пауза между отправками
            time.sleep(delay_between)

            # Длинная пауза каждые pause_after писем
            if count % pause_after == 0:
                print(f"⏳ Делаем паузу {long_pause} секунд после {count} писем...")
                time.sleep(long_pause)

        except Exception as e:
            skipped.append((email, f'Ошибка при отправке: {str(e)}'))

    result = {'sent': sent, 'skipped': skipped}
    send_control_email(client_config, result)
    shutil.rmtree(tmp_path, ignore_errors=True)
    return result
