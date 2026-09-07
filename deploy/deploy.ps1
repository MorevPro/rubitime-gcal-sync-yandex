# Деплой функций rubitime-gcal-sync-yandex через Yandex Cloud CLI (yc).
# Заполните переменные ниже под свой каталог (folder) и облако, затем запускайте по шагам.
#
# Требования: установлен и авторизован `yc` (yc init), выбран folder-id по умолчанию
# либо передавайте --folder-id явно.

$ErrorActionPreference = "Stop"

# --- Настройки: заполните под себя ---------------------------------------
$FolderId          = "<your-folder-id>"
$ServiceAccountId  = "<function-service-account-id>"   # сервисный аккаунт ЯО для самой функции (не Google!)
$WebhookFnName     = "webhook-62s55swk349fbv4pzn4qjtbn23rctsuk"
$ScheduleFnName    = "rubitime-gcal-schedule"
$Runtime           = "python312"
$Memory            = "128m"
$WebhookTimeout    = "30s"
$ScheduleTimeout   = "300s"
$EnvFile           = ".env"                              # источник переменных ниже
# ---------------------------------------------------------------------------

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Не найден файл $EnvFile. Скопируйте .env.example в .env и заполните значения."
}

foreach ($value in @($FolderId, $ServiceAccountId, $WebhookFnName, $ScheduleFnName)) {
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

# Google принимает JSON-ключ как сырой JSON или base64. Для функции используем
# однострочное base64-значение, чтобы JSON не ломал аргумент --environment.
$googleKeyFile = Get-EnvValue 'GOOGLE_SERVICE_ACCOUNT_JSON_FILE'
if ([string]::IsNullOrWhiteSpace($googleKeyFile)) {
    throw "В $EnvFile не заполнена GOOGLE_SERVICE_ACCOUNT_JSON_FILE."
}
if (-not [IO.Path]::IsPathRooted($googleKeyFile)) {
    $googleKeyFile = Join-Path (Get-Location) $googleKeyFile
}
if (-not (Test-Path -LiteralPath $googleKeyFile -PathType Leaf)) {
    throw "Не найден файл Google service account: $googleKeyFile"
}
$googleServiceAccountJson = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes((Get-Content -LiteralPath $googleKeyFile -Raw))
)

function Optional-EnvValue($key, $default) {
    $value = Get-EnvValue $key
    if ([string]::IsNullOrWhiteSpace($value)) { return $default }
    return $value
}

# Все настройки передаются в environment обеих функций.
$envVars = @(
    "GOOGLE_CALENDAR_ID=$(Require-EnvValue 'GOOGLE_CALENDAR_ID')",
    "GOOGLE_SERVICE_ACCOUNT_JSON=$googleServiceAccountJson",
    "GOOGLE_EVENT_COLOR_ID=$(Optional-EnvValue 'GOOGLE_EVENT_COLOR_ID' '5')",
    "GOOGLE_LONG_EVENT_COLOR_ID=$(Optional-EnvValue 'GOOGLE_LONG_EVENT_COLOR_ID' '9')",
    "RUBITIME_API_KEY=$(Require-EnvValue 'RUBITIME_API_KEY')",
    "RUBITIME_BRANCH_ID=$(Require-EnvValue 'RUBITIME_BRANCH_ID')",
    "RUBITIME_COOPERATOR_ID=$(Require-EnvValue 'RUBITIME_COOPERATOR_ID')",
    "RUBITIME_SERVICE_ID=$(Require-EnvValue 'RUBITIME_SERVICE_ID')",
    "RUBITIME_ONLY_AVAILABLE=$(Optional-EnvValue 'RUBITIME_ONLY_AVAILABLE' 'true')",
    "EVENT_TIMEZONE=$(Optional-EnvValue 'EVENT_TIMEZONE' 'Europe/Moscow')",
    "EVENT_LOCATION=$(Optional-EnvValue 'EVENT_LOCATION' 'Москва')",
    "CALENDAR_SUMMARY_TEMPLATE=$(Optional-EnvValue 'CALENDAR_SUMMARY_TEMPLATE' '{payment_prefix}{name} | {price}')",
    "LOG_LEVEL=$(Optional-EnvValue 'LOG_LEVEL' 'INFO')"
) -join ","

function Get-OrCreateFunction($name) {
    $existing = yc serverless function get --name $name --folder-id $FolderId --format json 2>$null | ConvertFrom-Json
    if ($existing -and $existing.id) {
        Write-Host "Используется существующая функция $name ($($existing.id))"
        return $existing.id
    }
    Write-Host "Создание функции $name"
    $created = yc serverless function create --name $name --folder-id $FolderId --format json | ConvertFrom-Json
    return $created.id
}

$webhookFunctionId = Get-OrCreateFunction $WebhookFnName
$scheduleFunctionId = Get-OrCreateFunction $ScheduleFnName

Write-Host "== Публикация версии: webhook =="
yc serverless function version create `
    --function-id $webhookFunctionId `
    --folder-id $FolderId `
    --runtime $Runtime `
    --entrypoint webhook_handler.handler `
    --memory $Memory `
    --execution-timeout $WebhookTimeout `
    --source-path . `
    --service-account-id $ServiceAccountId `
    --environment $envVars

Write-Host "== Публикация версии: schedule =="
yc serverless function version create `
    --function-id $scheduleFunctionId `
    --folder-id $FolderId `
    --runtime $Runtime `
    --entrypoint schedule_handler.handler `
    --memory $Memory `
    --execution-timeout $ScheduleTimeout `
    --source-path . `
    --service-account-id $ServiceAccountId `
    --environment $envVars

Write-Host "== Публичный доступ к webhook-функции (allUsers -> functions.functionInvoker) =="
yc serverless function allow-unauthenticated-invoke --id $webhookFunctionId

Write-Host "== Доступ Timer к schedule-функции =="
yc serverless function add-access-binding `
    --id $scheduleFunctionId `
    --role functions.functionInvoker `
    --service-account-id $ServiceAccountId

Write-Host "== Проверка Timer-триггера =="
$timer = yc serverless trigger list --folder-id $FolderId --format json | ConvertFrom-Json |
    Where-Object { $_.name -eq "rubitime-gcal-schedule-timer" }
if (-not $timer) {
    yc serverless trigger create timer `
        --name rubitime-gcal-schedule-timer `
        --folder-id $FolderId `
        --cron-expression "*/5 * * * ? *" `
        --invoke-function-id $scheduleFunctionId `
        --invoke-function-service-account-id $ServiceAccountId
} else {
    Write-Host "Timer уже существует ($($timer.id)), создание пропущено"
}

Write-Host "Готово. URL webhook-функции:"
yc serverless function get --id $webhookFunctionId --format json | Select-String "http_invoke_url"
