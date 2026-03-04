# Sipuni Call Analyzer

Автоматический анализ звонков менеджеров по продажам. Приложение принимает вебхуки от Сипуни, транскрибирует аудио через Groq Whisper (быстро и дёшево), анализирует через GPT-4o и отправляет фидбек в Telegram.

## Что это

Sipuni Call Analyzer — это сервис, который:
- Принимает вебхуки от телефонии Сипуни о завершённых звонках
- Скачивает аудиозапись разговора
- Транскрибирует через Groq Whisper large-v3 (в 10 раз дешевле OpenAI Whisper)
- Анализирует качество звонка через GPT-4o по 5 критериям
- Отправляет подробный фидбек менеджеру в Telegram

## Архитектура

```
┌─────────┐      POST /webhook       ┌──────────────────┐
│ Сипуни  │  ───────────────────────▶│  FastAPI Server  │
└─────────┘                          └────────┬─────────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │  Скачивание    │
                                     │  аудиофайла    │
                                     └────────┬───────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │ Groq Whisper   │
                                     │  large-v3      │
                                     └────────┬───────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │ OpenAI GPT-4o  │
                                     │    Анализ      │
                                     └────────┬───────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │   Telegram     │
                                     │    Фидбек      │
                                     └────────────────┘
```

## Быстрый старт

### 1. Клонирование репозитория

```bash
git clone <repo-url>
cd call_analyzer
```

### 2. Настройка переменных окружения

```bash
cp .env.example .env
```

Отредактируйте `.env` и заполните все переменные:

```env
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
SIPUNI_API_KEY=your_sipuni_api_key
```

### 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4. Запуск

```bash
uvicorn main:app --reload
```

Сервер запустится на `http://localhost:8000`

### 5. Проверка

```bash
curl http://localhost:8000/health
```

Ответ: `{"status": "ok", "timestamp": "..."}`

## Получение API-ключей

### Groq API (транскрибация)

1. Зарегистрируйтесь на [console.groq.com](https://console.groq.com)
2. Перейдите в раздел API Keys
3. Создайте новый ключ
4. Скопируйте ключ (начинается с `gsk_`)

Groq Whisper large-v3 — быстрее и дешевле OpenAI Whisper.

### OpenAI API (анализ)

1. Зарегистрируйтесь на [platform.openai.com](https://platform.openai.com)
2. Перейдите в [API Keys](https://platform.openai.com/api-keys)
3. Создайте новый ключ
4. Скопируйте ключ (начинается с `sk-`)

### Telegram Bot Token

1. Откройте Telegram и найдите [@BotFather](https://t.me/BotFather)
2. Отправьте `/newbot`
3. Следуйте инструкциям (имя бота, username)
4. Скопируйте токен (формат: `123456789:ABC...`)

### Telegram Chat ID

Способ 1 — через @userinfobot:
1. Откройте [@userinfobot](https://t.me/userinfobot)
2. Отправьте любое сообщение
3. Бот ответит вашим Chat ID

Способ 2 — через @getmyid_bot:
1. Откройте [@getmyid_bot](https://t.me/getmyid_bot)
2. Нажмите Start
3. Скопируйте Your user ID

Для группового чата:
1. Добавьте бота в группу
2. Отправьте любое сообщение
3. Откройте: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Найдите chat.id (отрицательное число для групп)

## Настройка Сипуни

1. Войдите в [личный кабинет Сипуни](https://sipuni.com)
2. Перейдите в **Интеграции** → **Вебхуки**
3. Создайте новый вебхук:
   - **URL**: `https://your-domain.railway.app/webhook`
   - **Метод**: POST
   - **Событие**: Завершение звонка (call_completed)
   - **Включить отправку ссылки на запись**: Да
4. Сохраните и скопируйте API-ключ для переменной `SIPUNI_API_KEY`

## Деплой на Railway

### Через GitHub

1. Запушьте код в GitHub репозиторий
2. Зайдите на [railway.app](https://railway.app)
3. Создайте новый проект → Deploy from GitHub repo
4. Выберите репозиторий
5. Добавьте переменные окружения в Settings → Variables
6. Railway автоматически задеплоит приложение

### Через Railway CLI

```bash
# Установка CLI
npm install -g @railway/cli

# Авторизация
railway login

# Инициализация проекта
railway init

# Добавление переменных
railway variables set GROQ_API_KEY=gsk_...
railway variables set OPENAI_API_KEY=sk-...
railway variables set TELEGRAM_BOT_TOKEN=123456:ABC...
railway variables set TELEGRAM_CHAT_ID=123456789
railway variables set SIPUNI_API_KEY=your_key

# Деплой
railway up
```

## Переменные окружения

| Переменная | Описание | Пример |
|------------|----------|--------|
| `GROQ_API_KEY` | API-ключ Groq для транскрибации | `gsk_abc123...` |
| `OPENAI_API_KEY` | API-ключ OpenAI для анализа GPT-4o | `sk-abc123...` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | ID чата для уведомлений | `123456789` |
| `SIPUNI_API_KEY` | API-ключ Сипуни для авторизации | `your_key` |

## Структура проекта

```
call_analyzer/
├── main.py              # FastAPI приложение, вебхук-эндпоинты
├── analyzer.py          # Транскрипция (Groq) + анализ (GPT-4o)
├── telegram_bot.py      # Отправка сообщений в Telegram
├── .env.example         # Шаблон переменных окружения
├── requirements.txt     # Python-зависимости
├── Dockerfile           # Docker-образ
├── railway.toml         # Конфиг деплоя на Railway
└── README.md            # Документация
```

## API Endpoints

### GET /health

Health-check эндпоинт для мониторинга.

```json
{"status": "ok", "timestamp": "2025-02-27T10:30:00"}
```

### POST /webhook

Принимает вебхук от Сипуни. Возвращает 200 OK сразу, обработка в фоне.

Условия обработки:
- `status == "answered"` — звонок отвечен
- `duration >= 60` — длительность от 60 секунд
- `recording_url` — есть ссылка на запись

## Формат анализа

Анализ звонка включает оценку по 5 этапам:

1. 📞 **Приветствие** — оценка 1-10
2. 🔍 **Выявление потребностей** — оценка 1-10
3. 🎯 **Презентация** — оценка 1-10
4. 🛡 **Работа с возражениями** — оценка 1-10
5. 🤝 **Закрытие** — оценка 1-10

Итого:
- Общая оценка X/10
- Топ-3 сильных стороны
- Топ-3 зоны роста
- Главная рекомендация

## Локальное тестирование

Для тестирования без Сипуни можно отправить POST-запрос вручную:

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_sipuni_api_key" \
  -d '{
    "call_id": "test123",
    "recording_url": "https://example.com/test.mp3",
    "duration": 120,
    "caller_number": "+79001234567",
    "called_number": "+74951234567",
    "direction": "incoming",
    "manager_name": "Иван Петров",
    "call_start": "2025-02-27T10:30:00",
    "status": "answered"
  }'
```

## Лицензия

MIT
