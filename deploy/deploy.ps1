# Деплой функций rubitime-gcal-sync-yandex через Yandex Cloud CLI (yc).
# Заполните переменные ниже под свой каталог (folder) и облако, затем запускайте по шагам.
#
# Требования: установлен и авторизован `yc` (yc init), выбран folder-id по умолчанию
# либо передавайте --folder-id явно.

$ErrorActionPreference = "Stop"

# --- Настройки: заполните под себя ---------------------------------------
$FolderId          = "<your-folder-id>"
$ServiceAccountId  = "<function-service-account-id>"   # сервисный аккаунт ЯО для самой функции (не Google!)
$WebhookFnName     = "rubitime-gcal-webhook"
$ScheduleFnName    = "rubitime-gcal-schedule"
$Runtime           = "python312"
$Memory            = "128m"
$WebhookTimeout    = "10s"
$ScheduleTimeout   = "60s"
$EnvFile           = ".env"                              # используется как источник переменных ниже
$LockboxSecretId   = "<lockbox-secret-id>"
$LockboxVersionId  = "<version-id>"                       # можно оставить пустым для текущей версии
$LockboxSecretKey  = "<key-in-secret>"
# ---------------------------------------------------------------------------

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Не найден файл $EnvFile. Скопируйте .env.example в .env и заполните значения."
}

foreach ($value in @($FolderId, $ServiceAccountId, $LockboxSecretId, $LockboxSecretKey)) {
    if ([string]::IsNullOrWhiteSpace($value) -or $value.StartsWith("<")) {
        throw "Заполните обязательные параметры в начале deploy/deploy.ps1."
    }
}

function Get-EnvValue($key) {
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return ($line -split "=", 2)[1].Trim()
}

function Require-EnvValue($key) {
    $value = Get-EnvValue $key
    if ([string]::IsNullOrWhiteSpace($value) -or $value -eq "XXX") {
        throw "В $EnvFile не заполнена обязательная переменная $key."
    }
    return $value
}

# Переменные окружения, пробрасываемые в обе функции.
# ВАЖНО: GOOGLE_SERVICE_ACCOUNT_JSON держите в Lockbox-секрете, а не в открытом виде,
# см. README.md -> "Секреты и переменные окружения". Ниже — вариант "напрямую в env"
# для быстрого старта.
$envVars = @(
    "GOOGLE_CALENDAR_ID=$(Require-EnvValue 'GOOGLE_CALENDAR_ID')",
    "GOOGLE_EVENT_COLOR_ID=$(Get-EnvValue 'GOOGLE_EVENT_COLOR_ID')",
    "GOOGLE_LONG_EVENT_COLOR_ID=$(Get-EnvValue 'GOOGLE_LONG_EVENT_COLOR_ID')",
    "RUBITIME_API_KEY=$(Require-EnvValue 'RUBITIME_API_KEY')",
    "RUBITIME_BRANCH_ID=$(Require-EnvValue 'RUBITIME_BRANCH_ID')",
    "RUBITIME_COOPERATOR_ID=$(Require-EnvValue 'RUBITIME_COOPERATOR_ID')",
    "RUBITIME_SERVICE_ID=$(Require-EnvValue 'RUBITIME_SERVICE_ID')",
    "RUBITIME_ONLY_AVAILABLE=$(Get-EnvValue 'RUBITIME_ONLY_AVAILABLE')",
    "EVENT_TIMEZONE=$(Get-EnvValue 'EVENT_TIMEZONE')",
    "EVENT_LOCATION=$(Get-EnvValue 'EVENT_LOCATION')",
    "CALENDAR_SUMMARY_TEMPLATE=$(Get-EnvValue 'CALENDAR_SUMMARY_TEMPLATE')",
    "LOG_LEVEL=$(Get-EnvValue 'LOG_LEVEL')"
) -join ","

$secretSpec = "environment-variable=GOOGLE_SERVICE_ACCOUNT_JSON,id=$LockboxSecretId,key=$LockboxSecretKey"
if (-not [string]::IsNullOrWhiteSpace($LockboxVersionId) -and -not $LockboxVersionId.StartsWith("<")) {
    $secretSpec += ",version-id=$LockboxVersionId"
}

Write-Host "== Создание функций (один раз) =="
yc serverless function create --name $WebhookFnName --folder-id $FolderId
yc serverless function create --name $ScheduleFnName --folder-id $FolderId

Write-Host "== Публикация версии: webhook =="
yc serverless function version create `
    --function-name $WebhookFnName `
    --folder-id $FolderId `
    --runtime $Runtime `
    --entrypoint webhook_handler.handler `
    --memory $Memory `
    --execution-timeout $WebhookTimeout `
    --source-path . `
    --service-account-id $ServiceAccountId `
    --secret $secretSpec `
    --environment $envVars

Write-Host "== Публикация версии: schedule =="
yc serverless function version create `
    --function-name $ScheduleFnName `
    --folder-id $FolderId `
    --runtime $Runtime `
    --entrypoint schedule_handler.handler `
    --memory $Memory `
    --execution-timeout $ScheduleTimeout `
    --source-path . `
    --service-account-id $ServiceAccountId `
    --secret $secretSpec `
    --environment $envVars

Write-Host "== Публичный доступ к webhook-функции (allUsers -> functions.functionInvoker) =="
yc serverless function allow-unauthenticated-invoke --name $WebhookFnName --folder-id $FolderId

Write-Host "== Доступ Timer к schedule-функции =="
yc serverless function add-access-binding `
    --name $ScheduleFnName `
    --folder-id $FolderId `
    --role functions.functionInvoker `
    --service-account-id $ServiceAccountId

Write-Host "== Timer-триггер для schedule-функции (каждые 5 минут) =="
yc serverless trigger create timer `
    --name rubitime-gcal-schedule-timer `
    --folder-id $FolderId `
    --cron-expression "*/5 * * * ? *" `
    --invoke-function-name $ScheduleFnName `
    --invoke-function-service-account-id $ServiceAccountId

Write-Host "Готово. URL webhook-функции:"
yc serverless function get --name $WebhookFnName --folder-id $FolderId --format json | Select-String "http_invoke_url"
