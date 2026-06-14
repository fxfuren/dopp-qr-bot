# DOPP QR Bot

Telegram бот для извлечения QR-кодов из PDF документов ДОПП, хранящихся на Яндекс.Диске.

## Возможности

- Поиск PDF файлов на Яндекс.Диске по номеру спецификации
- Извлечение QR-кодов из найденных документов
- Отправка QR-кодов в виде изображений в Telegram
- Уведомления в чат о запросах QR-кодов от водителей (с информацией о водителе, СМР и автомобиле)

## Требования

- Docker и Docker Compose
- Telegram Bot Token (получить у [@BotFather](https://t.me/BotFather))
- Yandex Disk OAuth Token (получить на [oauth.yandex.ru](https://oauth.yandex.ru/))

## Быстрый старт

### 1. Клонирование репозитория

```bash
git clone https://github.com/fxfuren/dopp-qr-bot
cd dopp-qr-bot
```

### 2. Настройка окружения

Скопируйте `.env.example` в `.env` и заполните необходимые значения:

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
BOT_TOKEN=your_telegram_bot_token
YANDEX_DISK_TOKEN=your_yandex_disk_token
YADISK_FOLDER=/dopps
TMP_DIR=/tmp/dopp_bot
QR_DPI=150
LOG_LEVEL=INFO

# Опционально: ID чата для уведомлений о запросах QR-кодов
# NOTIFICATION_CHAT_ID=-1001234567890
```

### 3. Запуск

```bash
# Сборка и запуск
docker compose up -d

# Проверка здоровья
docker inspect dopp-qr-bot --format='{{.State.Health.Status}}'

# Просмотр логов
docker compose logs -f
```

## Полезные команды

```bash
docker compose up -d                    # Запустить
docker compose down                     # Остановить
docker compose logs -f                  # Логи
docker compose restart                  # Перезапустить
docker compose ps                       # Статус

# Healthcheck
docker inspect dopp-qr-bot --format='{{.State.Health.Status}}'

# Cleanup
docker compose down -v --rmi local      # Удалить все
```

## Production Best Practices

### Безопасность

- ✅ Non-root пользователь (UID 1000)
- ✅ Read-only filesystem
- ✅ No new privileges
- ✅ Minimal base image (python:3.13-slim)
- ✅ Multi-stage build для уменьшения размера образа

### Мониторинг

- ✅ Healthcheck с проверкой PID и heartbeat
- ✅ Graceful shutdown (SIGTERM/SIGINT)
- ✅ Structured logging с ротацией
- ✅ Resource limits (CPU/Memory)

### Надежность

- ✅ Restart policy: unless-stopped
- ✅ Init system (tini) для правильной обработки сигналов
- ✅ Stop grace period 30s
- ✅ Healthcheck с retries

### Логирование

Логи сохраняются в `/tmp/dopp_bot/logs/` внутри контейнера:

- Ротация при достижении 10 MB
- Хранение за последние 7 дней
- Автоматическое сжатие старых логов

Для просмотра логов:

```bash
# Логи контейнера
docker compose logs -f

# Логи приложения (внутри volume)
docker exec dopp-qr-bot cat /tmp/dopp_bot/logs/bot.log
```

## Использование бота

1. Запустите бота командой `/start`
2. Отправьте номер спецификации (например: `47589`)
3. Бот найдет PDF файл на Яндекс.Диске и извлечет QR-коды
4. QR-коды будут отправлены в виде изображений

## Настройка уведомлений в чат

Если вы хотите получать уведомления о запросах QR-кодов от водителей в отдельный чат:

### 1. Получение Chat ID

**Способ 1: Через команду бота /chatid (Рекомендуется)**
1. Добавьте вашего бота в нужный чат
2. Отправьте команду `/chatid` в этом чате
3. Бот покажет Chat ID и готовую строку для .env
4. Скопируйте Chat ID

**Способ 2: Через бота @userinfobot**
1. Добавьте бота [@userinfobot](https://t.me/userinfobot) в ваш чат
2. Бот автоматически отправит Chat ID
3. Удалите бота из чата (опционально)

**Способ 3: Через бота @getidsbot**
1. Добавьте бота [@getidsbot](https://t.me/getidsbot) в ваш чат
2. Отправьте команду `/start@getidsbot` в чате
3. Бот покажет Chat ID

**Способ 4: Вручную через API**
1. Добавьте вашего бота в чат
2. Отправьте любое сообщение в чат
3. Откройте в браузере: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
4. Найдите `"chat":{"id":-1001234567890}` в ответе

### 2. Настройка прав бота

**Важно:** Бот должен иметь права на отправку сообщений в чат:

- **Для групп/супергрупп:** Добавьте бота как администратора или убедитесь, что все участники могут писать
- **Для каналов:** Добавьте бота как администратора с правом публиковать сообщения

### 3. Добавление Chat ID в .env

```env
NOTIFICATION_CHAT_ID=-1005231715201
```

**Примечание:** Chat ID для групп/супергрупп/каналов всегда начинается с `-100`

### 4. Перезапуск бота

```bash
docker compose restart
```

### Формат уведомлений

Когда водитель запрашивает QR-код, в чат придет уведомление:

```
Водитель запросил QR-код и успешно его получил

👤 Водитель: @username (или имя, или ID)
📄 СМР: 47589
🚗 АВТО: А123БВ777
🚛 ПРИЦЕП: АВ1234-56
```

## Структура проекта

```
dopp-qr-bot/
├── src/
│   ├── __init__.py
│   ├── main.py              # Точка входа
│   ├── config.py            # Конфигурация
│   ├── handlers.py          # Telegram handlers
│   ├── pdf_processor.py     # Обработка PDF
│   └── yadisk_client.py     # Клиент Яндекс.Диска
├── Dockerfile               # Production Dockerfile
├── docker-compose.yml       # Docker Compose config
├── healthcheck.py           # Healthcheck script
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
└── README.md
```

## Troubleshooting

### Проверка здоровья контейнера

```bash
docker inspect dopp-qr-bot --format='{{.State.Health.Status}}'
```

### Просмотр логов

```bash
# Все логи
docker compose logs -f

# Последние 100 строк
docker compose logs --tail=100

# Логи приложения
docker exec dopp-qr-bot tail -f /tmp/dopp_bot/logs/bot.log
```

### Перезапуск при проблемах

```bash
# Мягкий перезапуск
docker compose restart

# Полная пересборка
docker compose down
docker compose build --pull --no-cache
docker compose up -d
```

## Обновление

```bash
# Pull новых изменений
git pull

# Пересобрать и перезапустить
docker compose down
docker compose build --pull
docker compose up -d
```

## Деплой

```bash
# 1. Настрой .env
cp .env.example .env
# Заполни BOT_TOKEN и YANDEX_DISK_TOKEN

# 2. Собери образ
docker compose build --pull

# 3. Запусти
docker compose up -d

# 4. Проверь
docker inspect dopp-qr-bot --format='{{.State.Health.Status}}'
docker compose logs -f
```

## Мониторинг в production

Рекомендуется настроить мониторинг:

1. **Docker healthcheck** - встроен в compose файл
2. **Логи** - используйте централизованную систему логирования (ELK, Loki)
3. **Метрики** - добавьте Prometheus exporter при необходимости
4. **Alerts** - настройте алерты на unhealthy состояние

## Лицензия

MIT
