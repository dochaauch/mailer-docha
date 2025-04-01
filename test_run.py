from mailer import load_clients_config, process_and_send_emails

# Загружаем конфигурации клиентов
clients = load_clients_config()

# Берём конкретного клиента по логину
client = clients['liivamae6ku']  # замени на нужный логин, если другой

# Запускаем рассылку
result = process_and_send_emails(client)

# Вывод результата
print("✅ Результат рассылки:")
print("Отправлено писем:", len(result['sent']))
print("Ошибки:")
for email, error in result['skipped']:
    print(f" - {email}: {error}")
