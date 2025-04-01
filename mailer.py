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
    service = build("sheets", "v4", credentials=credentials)

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
    return build('drive', 'v3', credentials=credentials)


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

    for _, row in df.iterrows():
        apt_number = str(row['apt_number']).strip()
        email = str(row.get('email', '')).strip()
        kr_nr = str(row.get('kr_nr', '')).strip()

        if not email:
            skipped.append(("", f"Отсутствует email для квартиры {apt_number}"))
            continue

        matched_file = next((fname for fname in pdf_map if apt_number in fname), None)
        if not matched_file:
            skipped.append((email, f"Файл PDF не найден по шаблону apt_number ({apt_number})"))
            continue

        ready.append({
            "apt_number": apt_number,
            "kr_nr": kr_nr,
            "email": email,
            "pdf": matched_file
        })

    return {
        "ready": ready,
        "skipped": skipped
    }
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

    for _, row in df.iterrows():
        apt_number = str(row['apt_number']).strip()
        email = str(row.get('email', '')).strip()
        kr_nr = str(row.get('kr_nr', '')).strip()

        if not email:
            skipped.append(("", f"Отсутствует email для квартиры {kr_nr}"))
            continue

        matched_file = next((fname for fname in pdf_map if apt_number in fname), None)
        if not matched_file:
            skipped.append((email, f"Файл PDF не найден по шаблону apt_number ({apt_number})"))
            continue

        ready.append({
            "apt_number": apt_number,
            "kr_nr": kr_nr,
            "email": email,
            "pdf": matched_file
        })

    return {
        "ready": ready,
        "skipped": skipped
    }




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

    tmp_path = os.path.join(BASE_DIR, 'tmp_pdf')
    os.makedirs(tmp_path, exist_ok=True)

    sent = []
    skipped = []

    for _, row in df.iterrows():
        apt_number = str(row['apt_number']).strip()
        email = str(row.get('email', '')).strip()
        kr_nr = str(row.get('kr_nr', '')).strip()
        full_address = client_config.get("address_prefix", "") + kr_nr

        if not email:
            skipped.append(("", f"Отсутствует email для квартиры {kr_nr}"))
            continue

        matched_file = next((fname for fname in pdf_map if apt_number in fname), None)
        if not matched_file:
            skipped.append((email, 'Файл PDF не найден по шаблону apt_number'))
            continue

        local_file = os.path.join(tmp_path, matched_file)

        try:
            download_pdf(pdf_map[matched_file], local_file, drive_service)

            raw_body = client_config['email_body']
            custom_body = raw_body \
                .replace("{{kr_nr}}", kr_nr) \
                .replace("{{full_address}}", full_address)

            start_time = time.time()

            send_email(
                user=client_config['email_user'],
                password=client_config['email_password'],
                to=email,
                subject=client_config['email_subject'],
                body=custom_body,
                attachment=local_file,
                bcc=client_config.get('email_bcc')
            )

            duration = time.time() - start_time
            print(f"✅ Email sent to {email} in {duration:.2f} seconds")

            sent.append(email)
        except Exception as e:
            skipped.append((email, f'Ошибка при отправке: {str(e)}'))

    result = {'sent': sent, 'skipped': skipped}
    send_control_email(client_config, result)

    shutil.rmtree(tmp_path, ignore_errors=True)

    return result
