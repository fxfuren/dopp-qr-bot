# DOPP QR Bot

Telegram бот для автоматического извлечения QR-кодов из PDF документов ДОПП, хранящихся на Яндекс.Диске.

## 🚀 Возможности

- ✅ Автоматический поиск PDF по номеру спецификации
- ✅ Поиск номера спецификации по всем страницам документа
- ✅ Автоматическое определение и извлечение QR-кода с помощью zxing-cpp
- ✅ Работает на Windows, Linux и macOS без дополнительных зависимостей
- ✅ Docker поддержка с современными best practices (2026)

## 📋 Требования

- Python 3.12+
- Telegram Bot Token (получить у [@BotFather](https://t.me/BotFather))
- Yandex Disk OAuth Token

## 🔧 Установка

### Локальный запуск

1. Клонируйте репозиторий
2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Создайте `.env` файл на основе `.env.example`:
```bash
cp .env.example .env
```

4. Заполните `.env` файл своими токенами

5. Запустите бота:
```bash
python -m src.main
```

### Docker запуск

1. Создайте `.env` файл с вашими токенами

2. Запустите через Docker Compose:
```bash
docker-compose up -d
```

3. Проверьте логи:
```bash
docker-compose logs -f bot
```

## 📝 Использование

1. Запустите бота командой `/start`
2. Отправьте номер спецификации (например: `47589`)
3. Бот найдет PDF на Яндекс.Диске и отправит QR-код

## 🔑 Получение токенов

### Telegram Bot Token

1. Напишите [@BotFather](https://t.me/BotFather)
2. Создайте нового бота командой `/newbot`
3. Скопируйте полученный токен

### Yandex Disk OAuth Token

1. Зарегистрируйте приложение на https://oauth.yandex.ru/
2. Укажите права доступа: `cloud_api:disk.read`
3. Получите токен по ссылке:
```
https://oauth.yandex.ru/authorize?response_type=token&client_id=YOUR_CLIENT_ID
```

## 🏗️ Архитектура

```
dopp-qr-bot/
├── src/
│   ├── main.py           # Точка входа
│   ├── config.py         # Конфигурация
│   ├── handlers.py       # Обработчики Telegram
│   ├── pdf_processor.py  # Обработка PDF и QR-кодов
│   └── yadisk_client.py  # Клиент Яндекс.Диска
├── Dockerfile            # Docker образ (Python 3.13)
├── docker-compose.yml    # Docker Compose конфигурация
└── requirements.txt      # Python зависимости
```

## 🔒 Безопасность

- ✅ Запуск от непривилегированного пользователя
- ✅ Read-only файловая система
- ✅ Ограничение ресурсов (CPU, память)
- ✅ Security options (no-new-privileges)
- ✅ Логирование с ротацией

## 📦 Технологии

- **python-telegram-bot** - Telegram Bot API
- **yadisk** - Yandex Disk API
- **PyMuPDF** - Обработка PDF
- **zxing-cpp** - Распознавание QR-кодов (без системных зависимостей)
- **pdfplumber** - Извлечение текста из PDF
- **loguru** - Логирование

## 📄 Лицензия

MIT

## 🤝 Поддержка

При возникновении проблем создайте Issue в репозитории.
