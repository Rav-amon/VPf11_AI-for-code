# Запускает проверку эндпоинтов через Python.
# PowerShell не исполняет .py из текущей папки без префикса python.
$script = Join-Path $PSScriptRoot 'check-endpoints.py'
& python $script @args
exit $LASTEXITCODE
