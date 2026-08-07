# Fail fast and avoid hidden PowerShell mistakes
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Run FastAPI locally with auto-reload (developer mode)
# --reload tells Uvicorn to watch your source files and automatically restart the server when code changes. That behavior is intended for development mode.
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload


# Run production-style locally, comment the dev mode above and uncomment the production mode below
# poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips "*"
