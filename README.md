# Rubitime -> Google Calendar

Serverless-сервис для синхронизации записей Rubitime и свободных слотов с Google Calendar. Сервис работает в Yandex Cloud Functions и состоит из двух функций:

- `webhook_handler.handler` принимает вебхуки Rubitime и создаёт, обновляет или удаляет события в Google Calendar.
- `schedule_handler.handler` периодически получает свободные слоты Rubitime и синхронизирует их с Google Calendar.

Serverless-версия [rubitime-gcal-sync](https://github.com/MorevPro/rubitime-gcal-sync-yandex/blob/rubitime-gcal-sync).

## Возможности

- Приём webhook-событий Rubitime через HTTPS.
- Создание записи в Google Calendar по `event-create-record`.
- Обновление записи по `event-update-record`.
- Удаление записи по `event-remove-record`.
- Автоматическое удаление события при `event-update-record` со статусом `4` (`Автоотмена через 15 минут` при неоплате).
- Идемпотентная синхронизация: если событие уже отсутствует при удалении, это считается успешным результатом.
- Синхронизация свободных слотов по расписанию Yandex Cloud Timer.
- Настройка часового пояса, адреса, цветов и шаблона названия события через переменные окружения.
- Подробные однострочные JSON-логи для диагностики webhook-обработки и Google Calendar API.

## Обработка webhook-событий

Для записи Rubitime с идентификатором `8986532` используется Google event ID `booked8986532`.

| Событие Rubitime | Действие |
|---|---|
| `event-create-record` | Создать событие; при дубликате выполнить обновление |
| `event-update-record` со статусом, отличным от `4` | Обновить событие; если оно отсутствует, создать его |
| `event-update-record` со статусом `4` | Удалить событие и не создавать его заново |
| `event-remove-record` | Удалить событие |

Статус `4` проверяется как числовое значение `4` и как строка `"4"`. Поэтому запись из webhook с полем `"status": 4` не будет превращена в событие с заголовком `❌`: существующее событие будет удалено вызовом Google Calendar API.

Обработчик webhook возвращает Rubitime успешный ответ о приёме после выполнения синхронизации. Внутренние ошибки синхронизации перехватываются и записываются в лог `webhook_processing_failed`; результат необходимо проверять по логам Yandex Cloud.

## Структура проекта

```text
rubitime-gcal-sync-yandex/
├── webhook_handler.py       # HTTP-функция приёма webhook-событий
├── schedule_handler.py      # функция синхронизации свободных слотов
├── app/
│   ├── config.py             # загрузка настроек из окружения и .env
│   ├── models/               # модели webhook и Google Calendar события
│   ├── routes/webhook.py     # обработка webhook-событий
│   ├── services/              # CalendarService и сервисы Rubitime
│   └── utils/                 # даты, логирование и обработка ошибок
├── tests/                    # автоматические тесты
├── scripts/local_invoke.py   # локальный вызов функций без деплоя
├── deploy/deploy.ps1         # команды деплоя в Yandex Cloud
├── requirements.txt           # зависимости runtime
├── requirements-dev.txt       # зависимости для разработки
├── .env.example               # пример локальной конфигурации
└── .yandexignore              # исключения из архива функции
```

## Настройка

Скопируйте `.env.example` в `.env` и заполните значения:

| Переменная | Обязательна | Значение по умолчанию | Описание |
|---|---:|---|---|
| `GOOGLE_CALENDAR_ID` | да | - | ID Google Calendar, например `xxxx@group.calendar.google.com` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | в функции | - | JSON-ключ Google service account, сырой JSON или base64 |
| `GOOGLE_SERVICE_ACCOUNT_JSON_FILE` | локально и для деплоя | - | Путь к JSON-файлу; deploy-скрипт преобразует его в base64 и передаёт в environment функции |
| `RUBITIME_API_KEY` | да | - | API-ключ Rubitime |
| `RUBITIME_BRANCH_ID` | да | `0` | ID филиала |
| `RUBITIME_COOPERATOR_ID` | да | `0` | ID сотрудника |
| `RUBITIME_SERVICE_ID` | да | `0` | ID услуги |
| `RUBITIME_ONLY_AVAILABLE` | нет | `true` | Запрашивать только свободные слоты |
| `EVENT_TIMEZONE` | нет | `Europe/Moscow` | Часовой пояс событий |
| `EVENT_LOCATION` | нет | `Москва` | Адрес события по умолчанию |
| `GOOGLE_EVENT_COLOR_ID` | нет | `5` | Цвет обычных событий |
| `GOOGLE_LONG_EVENT_COLOR_ID` | нет | `9` | Цвет событий длительностью более 60 минут |
| `CALENDAR_SUMMARY_TEMPLATE` | нет | `{payment_prefix}{name} | {price}` | Шаблон названия события |
| `LOG_LEVEL` | нет | `INFO` | Уровень логирования |

`RUBITIME_SYNC_INTERVAL_SECONDS` не используется. Интервал запуска задаётся cron-выражением Timer-триггера Yandex Cloud.

### Доступ Google Calendar

1. Создайте Google service account и JSON-ключ.
2. Откройте нужный календарь в Google Calendar и предоставьте service account доступ с правом изменения событий.
3. Для локальной разработки и деплоя укажите путь к файлу через `GOOGLE_SERVICE_ACCOUNT_JSON_FILE`.
4. `deploy/deploy.ps1` преобразует файл в base64 и сохраняет значение в environment обеих функций как `GOOGLE_SERVICE_ACCOUNT_JSON`.

Не добавляйте JSON-ключ в git. Значение `GOOGLE_SERVICE_ACCOUNT_JSON` хранится непосредственно в настройках функций Yandex Cloud; для усиления защиты при необходимости используйте закрытый проект или Lockbox.

## Локальный запуск

Требуется Python 3.12 или совместимая версия.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

После заполнения `.env` запустите тесты:

```powershell
python -m pytest -q
```

Ручной вызов webhook-функции без деплоя:

```powershell
python scripts/local_invoke.py webhook '{"event":"event-update-record","data":{"id":8986532,"parent_record":null,"name":"ИВАН ИВАНОВ ИВАНОВИЧ","record":"2026-09-06 11:00:00","status":4}}'
```

Для запуска синхронизации свободных слотов:

```powershell
python scripts/local_invoke.py schedule
```

## Деплой в Yandex Cloud

Требуются установленный и авторизованный [Yandex Cloud CLI](https://yandex.cloud/ru/docs/cli/quickstart) и доступ к нужному каталогу облака.

1. В начале `deploy/deploy.ps1` укажите `FolderId`, `ServiceAccountId` и имена функций.
2. Создайте `.env` на основе `.env.example` и заполните обязательные переменные.
3. Запустите `deploy/deploy.ps1` в PowerShell.
4. Скопируйте URL опубликованной webhook-функции в настройки webhook в Rubitime.

Скрипт создаёт две функции:

- webhook-функция с entrypoint `webhook_handler.handler`, публичным HTTP-вызовом и таймаутом `30s`.
- `rubitime-gcal-schedule` с entrypoint `schedule_handler.handler` и таймаутом `60s`.

Также скрипт выдаёт Timer доступ к schedule-функции и создаёт триггер `rubitime-gcal-schedule-timer`, запускающий синхронизацию каждые 5 минут (`*/5 * * * ? *`).

Скрипт находит существующие функции по имени и публикует новую версию. Если функция ещё не создана, она создаётся автоматически. Публичный доступ webhook-функции и доступ Timer переустанавливаются безопасно. Существующий Timer повторно не создаётся.

## Мониторинг

Проверка списка версий функции:

```powershell
yc serverless function version list --function-name <webhook-function-name>
```

Чтение логов за последний час:

```powershell
yc logging read --group-id <log-group-id> --since 1h
```

Полезные события логов:

- `webhook_received` - webhook получен.
- `webhook_accepted` - payload прошёл валидацию.
- `webhook_processing_started` - начата обработка записи.
- `google_calendar_request` и `google_calendar_response` - запрос и ответ Google Calendar API.
- `webhook_processing_finished` - обработка завершена успешно.
- `google_calendar_delete_idempotent` - событие уже отсутствовало при удалении.
- `webhook_processing_failed` и `google_calendar_error` - ошибка обработки или API.
- `rubitime_schedule_sync_finished` - завершена синхронизация свободных слотов.

Для кейса автоотмены ожидается `webhook_processing_finished` с `google_action: "delete"` и `outcome: "deleted"` либо `outcome: "already_absent"`. Записи с таким статусом не должны заканчиваться операцией `update` или `insert`.

## Лицензия

Лицензия проекта указана в файле `LICENSE`.
