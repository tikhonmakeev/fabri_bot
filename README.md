# Medical Intake Bot 🩺

Телеграм-бот для первичного медицинского анкетирования пациентов на предмет Болезни Фабри.

## Возможности

- ✅ Быстрое анкетирование с пошаговым диалогом
- ✅ Условные вопросы в зависимости от ответов
- ✅ Поддержка загрузки дополнительных документов и фото
- ✅ Отправка результатов администратору
- ✅ Работа в контейнере Docker

## Требования

- Docker и Docker Compose (для запуска в контейнере)
- или Python 3.11+ (для локального запуска)

## Быстрый старт

### Вариант 1: Запуск с Docker Compose (рекомендуется)

1. **Клонируйте репозиторий:**
```bash
git clone github.com/Keinedered/fabri_bot
cd fabri_bot
```

2. **Создайте файл `.env`** в корневой папке проекта:
```bash
BOT_TOKEN=your_telegram_bot_token_here
ADMIN_CHAT_ID=123456789  # (опционально) ID чата администратора
GROUP_CHAT_ID=-1003352487958
HOTLINE_PHONE=+7 (495) 123-45-67
CONSENT_DECLINE_PHONE=+7 (495) 123-45-67
LOG_CHAT_ID=-1003352487958
TEST_MODE=0
```

3. **Запустите бота:**
```bash
docker compose up -d
```

4. **Проверьте логи:**
```bash
docker compose logs
```

5. **Остановите бота:**
```bash
docker compose down
```

### Вариант 2: Запуск с помощью Dockerfile

1. **Подготовьте `.env` файл** (как в варианте 1)

2. **Соберите образ:**
```bash
docker build -t medical-bot .
```

3. **Запустите контейнер:**
```bash
docker run -d \
  --name medical-intake-bot \
  --restart always \
  --env-file .env \
  medical-bot
```

4. **Проверьте логи:**
```bash
docker logs -f medical-intake-bot
```

5. **Остановите контейнер:**
```bash
docker stop medical-intake-bot
docker rm medical-intake-bot
```

### Вариант 3: Локальный запуск (разработка)

1. **Установите зависимости:**
```bash
pip install -r requirements.txt
```

2. **Создайте файл `.env`:**
```
BOT_TOKEN=your_telegram_bot_token_here
ADMIN_CHAT_ID=123456789
GROUP_CHAT_ID=-1003352487958
HOTLINE_PHONE=+7 (495) 123-45-67
CONSENT_DECLINE_PHONE=+7 (495) 123-45-67
LOG_CHAT_ID=-1003352487958
TEST_MODE=0
```

3. **Запустите бота:**
```bash
python main.py
```

## Конфигурация

### Переменные окружения

Создайте файл `.env` в корневой папке с необходимыми переменными:

| Переменная | Описание | Обязательна |
|-----------|---------|-----------|
| `BOT_TOKEN` | Токен вашего Telegram бота | ✅ Да |
| `TEST_MODE` | Тестовый режим (`1`/`0`). При `0` бот работает в обычном режиме через `BOT_TOKEN`, `GROUP_CHAT_ID`, `LOG_CHAT_ID` | ❌ Нет (по умолчанию: `0`) |
| `ADMIN_CHAT_ID` | ID чата для отправки результатов анкет (бот должен иметь доступ к этому чату) | ❌ Нет |
| `GROUP_CHAT_ID` | ID группы для отправки анкет (по умолчанию: `-1003352487958`) | ❌ Нет |
| `LOG_CHAT_ID` | ID группы для отправки runtime-логов (по умолчанию: `-1003352487958`) | ❌ Нет |
| `HOTLINE_PHONE` | Номер горячей линии | ❌ Нет (дефолт: +7 (495) 123-45-67) |
| `CONSENT_DECLINE_PHONE` | Номер при отказе от согласия | ❌ Нет (по умолчанию совпадает с HOTLINE_PHONE) |

По умолчанию бот запущен в обычном режиме (`TEST_MODE=0`) и использует переменные `BOT_TOKEN`, `GROUP_CHAT_ID`, `LOG_CHAT_ID`.
Для тест-режима задайте `TEST_MODE=1` и передайте `TEST_BOT_TOKEN`, `TEST_GROUP_CHAT_ID`.

Если указан `ADMIN_CHAT_ID`, обязательно:
- добавить бота в этот чат (для групп/каналов);
- либо открыть диалог с ботом и нажать `/start` (для личного чата).

### Получение BOT_TOKEN

1. Откройте Telegram и найдите бота `@BotFather`
2. Отправьте команду `/newbot`
3. Следуйте инструкциям для получения токена
4. Скопируйте токен в переменную `BOT_TOKEN`

## Docker инструкции

### Просмотр логов

```bash
# Все логи
docker compose logs

# Последние 100 строк в реальном времени
docker compose logs -f --tail=100

# Логи специфического сервиса
docker logs -f medical-intake-bot
```

### Перезагрузка бота

```bash
# С использованием docker-compose
docker compose restart

# С использованием docker
docker restart medical-intake-bot
```

### Проверка статуса

```bash
# С использованием docker-compose
docker compose ps

# С использованием docker
docker ps | grep medical-intake-bot
```

### Удаление контейнера и образа

```bash
# С использованием docker-compose
docker compose down
docker image rm bottt_medical-bot

# С использованием docker
docker stop medical-intake-bot
docker rm medical-intake-bot
docker rmi medical-bot
```

## Структура проекта

```
bottt/
├── main.py              # Основной файл бота
├── requirements.txt     # Python зависимости
├── Dockerfile          # Docker image definition
├── docker-compose.yml  # Docker Compose конфигурация
├── .env                # Переменные окружения (создайте сами)
├── .env.example        # Пример .env файла
└── README.md          # Этот файл
```

## Зависимости

- **aiogram** 3.15.0 - Telegram Bot API фреймворк
- **python-dotenv** 1.0.1 - Работа с переменными окружения

## Решение проблем

### Ошибка: "BOT_TOKEN is not set"

❌ Проблема: Переменная окружения не установлена

✅ Решение:
1. Убедитесь, что файл `.env` существует в корневой папке
2. Откройте `.env` и проверьте, что `BOT_TOKEN` имеет значение
3. Для Docker перепроверьте флаг `--env-file .env`

### Ошибка: "Cannot connect to Docker daemon"

❌ Проблема: Docker не запущен

✅ Решение:
- Убедитесь, что Docker Desktop запущен
- На Linux: `sudo systemctl start docker`

### Бот не отвечает на сообщения

❌ Проблема: Возможные причины бывают разные

✅ Решение:
1. Проверьте логи: `docker compose logs -f`
2. Убедитесь, что BOT_TOKEN корректный
3. Проверьте интернет соединение контейнера
4. Перезагрузите: `docker compose restart`

## Развертывание на продакшене

### На VPS/Сервере

1. Установите Docker и Docker Compose
2. Загрузите проект на сервер
3. Установите переменные окружения в `.env`
4. Запустите: `docker compose up -d`
5. Настройте логирование и мониторинг

### На облачных платформах

**Google Cloud Run, AWS ECS, Heroku, Railway и другие** поддерживают развертывание Docker образов. Обратитесь к документации платформы.

## Безопасность

### ⚠️ Важно

- 🔐 **Никогда** не коммитьте `.env` файл в систему контроля версий
- 🔐 Используйте `.env.example` для документирования переменных
- 🔐 Храните `BOT_TOKEN` в защищенных местах
- 🔐 На продакшене используйте системы управления секретами (Kubernetes Secrets, HashiCorp Vault и т.д.)

### .gitignore

Убедитесь, что `.env` файл добавлен в `.gitignore`:

```
.env
.env.local
.DS_Store
__pycache__/
*.pyc
.pytest_cache/
logs/
```

## Логирование

Логи выводятся в консоль и доступны через:

```bash
docker compose logs -f
```

Формат логов:
```
2026-03-05 14:30:45,123 | INFO | medical_intake_bot | Starting bot polling...
```

## Поддержка

Если у вас есть вопросы или проблемы:

1. Проверьте раздел "Решение проблем"
2. Посмотрите логи: `docker compose logs`
3. Убедитесь, что все переменные окружения установлены
4. Проверьте актуальность версии Python (3.11+)

## Лицензия

Этот проект предоставляется как есть для медицинских целей.

## Автор

Medical Intake Bot • 2026
