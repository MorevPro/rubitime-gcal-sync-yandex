# Rubitime → Google Calendar (Yandex Cloud Functions)

Serverless-версия [rubitime-gcal-sync](../rubitime-gcal-sync): та же бизнес-логика
(вебхуки Rubitime и периодическая синхронизация свободных слотов в Google Calendar),
но развёрнутая как пара функций в Yandex Cloud Functions вместо постоянно работающего
FastAPI-сервиса в Docker.

## Что перенесено, а что изменилось

Код синхронизации (`app/services`, `app/models`, `app/utils`) перенесён **без изменений** —
он не зависел от FastAPI и работает одинаково в обоих проектах. Изменился только
транспортный слой:

| Было (FastAPI) | Стало (Yandex Cloud Functions) |
|---|---|
| `POST /webhook` в приложении, поднятом в Docker | функция `webhook_handler.handler`, вызываемая напрямую по HTTPS или через API Gateway |
| Фоновая asyncio-задача каждые `RUBITIME_SYNC_INTERVAL_SECONDS` | функция `schedule_handler.handler`, вызываемая по расписанию Yandex Cloud Trigger (Timer) |
| Ключ Google — файл, смонтированный в Docker (`GOOGLE_SERVICE_ACCOUNT_JSON_FILE`) | переменная окружения `GOOGLE_SERVICE_ACCOUNT_JSON` (рекомендуется — секрет Lockbox) |
| Ответ на вебхук мгновенный, синк идёт в фоне | ответ на вебхук возвращается **после** синхронизации с Google Calendar (функция не может продолжать работу после ответа) |

`process_webhook` по-прежнему сам перехватывает и логирует все исключения, поэтому
вебхук всегда отвечает `{"ok": true, "accepted": true, ...}`, даже если синхронизация
с Google Calendar внутри не удалась — как и в исходном сервисе.

`GET /health` как отдельный endpoint не нужен: обе функции проверяются вызовом
(`webhook_handler` отвечает на `GET` тем же `{"ok": true}`), а работоспособность —
через логи в Yandex Cloud Logging.

---

## Структура проекта

```
rubitime-gcal-sync-yandex/
├── webhook_handler.py       # Функция 1: приём вебхуков Rubitime
├── schedule_handler.py      # Функция 2: синк свободных слотов (по Timer-триггеру)
├── app/
│   ├── config.py            # Настройки из переменных окружения
│   ├── models/               # Pydantic-модели вебхука и события Google Calendar
│   ├── routes/webhook.py     # process_webhook — бизнес-логика обработки события
│   ├── services/              # CalendarService, EventFormatter, RubitimeScheduleSyncService
│   └── utils/                 # datetime-хелперы, парсинг ошибок Google API, логирование
├── tests/                     # pytest + один интерактивный скрипт (test_google_calendar.py)
├── scripts/local_invoke.py    # ручной вызов handler'ов без деплоя
├── deploy/deploy.ps1          # шаблон команд `yc` для создания функций и триггера
├── requirements.txt           # рантайм-зависимости (устанавливаются Yandex Cloud при сборке)
├── requirements-dev.txt       # + pytest, для локальной разработки
├── .yandexignore              # что не попадает в архив при деплое (см. ниже)
└── .env.example
```

---

## Переменные окружения

| Переменная | Обязательна | По умолчанию | Описание |
|------------|-------------|--------------|----------|
| `GOOGLE_CALENDAR_ID` | ✅ | — | ID календаря вида `xxxx@group.calendar.google.com` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ (в облаке) | — | JSON-ключ сервисного аккаунта Google (сырой JSON или base64) |
| `GOOGLE_SERVICE_ACCOUNT_JSON_FILE` | локально | — | Путь к файлу ключа — только для локального запуска/тестов |
| `RUBITIME_API_KEY` | ✅ | — | API ключ Rubitime |
| `RUBITIME_BRANCH_ID` | ✅ | — | ID филиала (> 0) |
| `RUBITIME_COOPERATOR_ID` | ✅ | — | ID сотрудника (> 0) |
| `RUBITIME_SERVICE_ID` | ✅ | — | ID услуги (> 0) |
| `RUBITIME_ONLY_AVAILABLE` | ❌ | `false` | `true` для выбора только свободных слотов |
| `EVENT_TIMEZONE` | ❌ | `Europe/Moscow` | Часовой пояс событий |
| `EVENT_LOCATION` | ❌ | — | Адрес по умолчанию для события |
| `GOOGLE_EVENT_COLOR_ID` | ❌ | `5` | Обычный цвет события в Google Calendar |
| `GOOGLE_LONG_EVENT_COLOR_ID` | ❌ | `9` | Синий цвет для записей длительностью более 60 минут |
| `CALENDAR_SUMMARY_TEMPLATE` | ❌ | `{payment_prefix}{name} | {price}` | Шаблон названий событий |
| `LOG_LEVEL` | ❌ | `INFO` | Уровень логирования (`INFO`/`DEBUG`) |

Интервал синхронизации свободных слотов (раньше `RUBITIME_SYNC_INTERVAL_SECONDS`)
больше не читается из `.env` — он задаётся расписанием Yandex Cloud Trigger
(`--cron-expression` в `deploy/deploy.ps1`).

### Секреты и переменные окружения

Ключ Google (`GOOGLE_SERVICE_ACCOUNT_JSON`) — чувствительные данные. Рекомендуется:

1. Создать секрет в **Yandex Lockbox** с версией, содержащей ключ Google целиком.
2. При создании версии функции подключить его как переменную окружения через `--secret`
   (см. пример в `deploy/deploy.ps1`) — тогда значение не попадает в открытые настройки
   функции и не светится в консоли/логах.

Остальные переменные (Rubitime API key и т.д.) можно передавать как обычные
`--environment` — они тоже не публичны, но не требуют отдельного секрет-хранилища.

---

## Локальная разработка

```bash
python -m venv .venv
. .venv/Scripts/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

cp .env.example .env
# заполните .env, положите рядом файл ключа Google (см. GOOGLE_SERVICE_ACCOUNT_JSON_FILE)

pytest
```

Ручной вызов обработчиков без деплоя:

```bash
python scripts/local_invoke.py webhook '{"event":"event-update-record","data":{"id":123,"parent_record":null,"name":"Test","record":"2026-08-20 15:00:00"}}'
python scripts/local_invoke.py schedule
```

---

## Деплой в Yandex Cloud

Требуется установленный и авторизованный [Yandex Cloud CLI](https://yandex.cloud/ru/docs/cli/quickstart)
(`yc init`).

1. Заполните переменные в начале `deploy/deploy.ps1` (`FolderId`, `ServiceAccountId`,
   идентификаторы секрета Lockbox).
2. Запустите скрипт по шагам (создание функций → публикация версий → публичный доступ
   к webhook-функции → доступ Timer → timer-триггер для schedule-функции).

Скрипт также выдаёт сервисному аккаунту функции роль `functions.functionInvoker` на
schedule-функцию, чтобы Timer мог её вызывать. Скрипт рассчитан на первичное создание
функций и Timer-триггера. При повторном полном
запуске команды `create` могут вернуть ошибку, если объекты уже существуют; для обновления
кода публикуйте новую версию функции, а существующий триггер повторно не создавайте.

Скрипт использует `--source-path .` — весь каталог проекта архивируется и заливается
как код функции; `.yandexignore` исключает из архива тесты, `.env`, ключи, README и
служебные файлы, оставляя только `app/`, `webhook_handler.py`, `schedule_handler.py`
и `requirements.txt`.

После деплоя:

- **Webhook**: укажите публичный HTTP-адрес `webhook_handler` (`yc serverless function get ...`)
  как URL вебхука в настройках Rubitime — как раньше указывался `https://your-domain.com/webhook`.
  Для кастомного домена/маршрутизации можно поставить перед функцией API Gateway.
- **Schedule**: ничего указывать не нужно — Timer-триггер сам вызывает `schedule_handler`
  по расписанию.

### Что проверено

- `pytest`: все 22 теста проходят.
- Реальный webhook create/update/delete успешно записал тестовое событие в Google Calendar,
  после проверки тестовое событие было удалено.
- Реальный вызов `schedule_handler` получил расписание Rubitime и синхронизировал свободные
  слоты в Google Calendar.
- Для Yandex Cloud проверены entrypoint'ы `webhook_handler.handler` и
  `schedule_handler.handler`, runtime `python312`, Lockbox-секрет и Timer cron.

Для записей с `duration > 60` используется цвет `GOOGLE_LONG_EVENT_COLOR_ID` (по умолчанию
`9`, синий blueberry). Запись длительностью ровно 60 минут остаётся в обычном цвете.

В `.env.example` намеренно нет значения `GOOGLE_SERVICE_ACCOUNT_JSON`: в Yandex Cloud
его нужно подключить из Lockbox через `deploy/deploy.ps1`. Для локального запуска используйте
`GOOGLE_SERVICE_ACCOUNT_JSON_FILE` или задайте JSON напрямую в окружении.

Важно: `webhook_handler` отвечает об успешном приёме даже если внутренняя синхронизация
завершилась ошибкой, поэтому после деплоя проверяйте `google_calendar_error` и
`webhook_processing_finished` в логах Yandex Cloud.

### Мониторинг

```bash
yc serverless function version list --function-name rubitime-gcal-webhook
yc logging read --group-id <log-group-id> --since 1h
```

Формат логов (однострочный JSON, structlog) не изменился — те же события
(`webhook_processing_finished`, `rubitime_schedule_sync_finished`,
`google_calendar_error` и т.д.), что и в исходном сервисе.

---

## Известные отличия от исходного сервиса

- Ответ на вебхук возвращается после завершения синка с Google Calendar (см. таблицу выше) —
  тайм-аут функции (`--execution-timeout`) должен быть больше, чем типичное время
  вызова Google Calendar API (по умолчанию в `deploy/deploy.ps1` — 10 секунд).
- Нет `/health`-эндпоинта отдельно — здоровье проверяется по логам/успешным вызовам.
- Нет Docker/docker-compose — деплой только через `yc` (Yandex Cloud CLI).
- В Google API-клиенте при запуске на Python 3.10 появляется предупреждение о скором
  прекращении поддержки; для Yandex Cloud выбран `python312`.
