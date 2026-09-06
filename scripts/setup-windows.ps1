# Frame: set up a machine from nothing.
#
#   powershell -ExecutionPolicy Bypass -File setup-windows.ps1
#
# Written for a laptop belonging to someone who is not a developer, driven over
# remote access. It is safe to run twice: every step checks before it acts, so a
# failure part-way is fixed by rerunning rather than by unpicking anything.
#
# It refuses to seed the development accounts. Those have passwords published in
# the repository, and the admin panel is served on the same public hostname as
# the guest app, so seeding them would put an open door on the internet. A real
# admin account with a generated password is created instead, and the password
# is printed exactly once.

param(
    # The repository is private, so a bare clone on a machine that has never
    # signed in will stop on a credential prompt. Pass a read-only fine-grained
    # token instead of signing GitHub in on someone else's laptop:
    #
    #   -Token github_pat_xxx
    #
    # The token is used for the clone and never written to disk.
    [string]$Token = '',
    [string]$RepoUrl = 'https://github.com/Noel9907/Luna.git',
    [string]$Hostname = 'https://frame.venusvision.in'
)

$ErrorActionPreference = 'Stop'

$Root = Join-Path $env:USERPROFILE 'frame'

function Say  ($m) { Write-Host "  $m" }
function Good ($m) { Write-Host "  OK    $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad  ($m) { Write-Host "  FAIL  $m" -ForegroundColor Red }

function Have ($exe) {
    $c = Get-Command $exe -ErrorAction SilentlyContinue
    if ($null -eq $c) { return $false }
    return $true
}

# Runs a native command quietly and returns its exit code.
#
# Not `cmd *> $null`. In PowerShell 5.1 redirecting a native program's stderr
# wraps each line in a NativeCommandError, and with ErrorActionPreference set to
# Stop that terminates the script. Docker prints warnings to stderr while
# working perfectly, so the plain form aborts setup on a healthy machine and the
# message blames the wrong thing entirely.
function Quiet {
    param([string]$Exe, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | Out-Null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

Write-Host ""
Write-Host "  Frame setup" -ForegroundColor Cyan
Write-Host "  ===========" -ForegroundColor Cyan
Write-Host ""

# ── 1. prerequisites ──────────────────────────────────────────────────
# Checked all together and reported at once. Discovering a missing tool three
# steps in, over remote access, wastes far more time than one upfront pass.

Say "checking what is installed"
$missing = @()
foreach ($t in @(
    @{ n = 'git';    id = 'Git.Git' },
    @{ n = 'python'; id = 'Python.Python.3.12' },
    @{ n = 'node';   id = 'OpenJS.NodeJS.LTS' },
    @{ n = 'docker'; id = 'Docker.DockerDesktop' }
)) {
    if (Have $t.n) { Good $t.n } else { Bad "$($t.n) is missing"; $missing += $t }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    if (Have 'winget') {
        Say "install the missing ones with:"
        Write-Host ""
        foreach ($m in $missing) { Write-Host "    winget install --id $($m.id) -e" }
        Write-Host ""
        Say "then CLOSE this window, open a new one, and run this script again."
        Say "(a new window is required so the new tools are on PATH)"
    } else {
        Say "winget is not available. Install by hand:"
        Write-Host "    git     https://git-scm.com/download/win"
        Write-Host "    python  https://www.python.org/downloads/   (tick 'Add to PATH')"
        Write-Host "    node    https://nodejs.org/en/download"
        Write-Host "    docker  https://www.docker.com/products/docker-desktop/"
    }
    Write-Host ""
    exit 1
}

# Docker Desktop can be installed but not started, which fails differently and
# more confusingly than not being installed at all.
Say "checking Docker is actually running"
if ((Quiet 'docker' @('info')) -ne 0) {
    Bad "Docker Desktop is installed but not running."
    Say "Start Docker Desktop, wait for the whale icon to stop animating, rerun this."
    Say "If it refuses to start, virtualization is probably off in the BIOS."
    exit 1
}
Good "docker is running"

# ── 2. the code ───────────────────────────────────────────────────────

if (Test-Path (Join-Path $Root '.git')) {
    Say "updating existing checkout at $Root"
    if ($Token) {
        git -C $Root pull --ff-only ($RepoUrl -replace '^https://', "https://$Token@") main
    } else {
        git -C $Root pull --ff-only
    }
    if (-not $?) { Bad "git pull failed"; exit 1 }
} else {
    Say "cloning into $Root"
    $cloneUrl = $RepoUrl
    if ($Token) { $cloneUrl = $RepoUrl -replace '^https://', "https://$Token@" }
    git clone $cloneUrl $Root
    if (-not $?) {
        Bad "clone failed."
        Say "This repository is private. Create a read-only fine-grained token at"
        Say "github.com/settings/tokens, then rerun with:  -Token github_pat_xxx"
        exit 1
    }
    if ($Token) {
        # The token would otherwise sit in .git/config on his machine forever.
        git -C $Root remote set-url origin $RepoUrl
        Say "clone token cleared from git config"
    }
}
Good "code is in $Root"

$Backend = Join-Path $Root 'backend'

# ── 3. dependencies ───────────────────────────────────────────────────

Say "installing python packages (a few minutes)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r (Join-Path $Backend 'requirements.txt')
if (-not $?) { Bad "pip install failed"; exit 1 }
Good "python packages"

Say "installing node packages (a few minutes)"
Push-Location $Root
npm install --no-audit --no-fund
$npmOk = $?
Pop-Location
if (-not $npmOk) { Bad "npm install failed"; exit 1 }
Good "node packages"

# ── 4. models ─────────────────────────────────────────────────────────
# ~255MB for AuraFace. Skipped automatically if already present.

Say "downloading face models (255MB, slow on a poor connection)"
Push-Location $Backend
python scripts/download_models.py --auraface
$modelsOk = $?
Pop-Location
if (-not $modelsOk) { Bad "model download failed"; exit 1 }
Good "models"

# ── 5. configuration ──────────────────────────────────────────────────
# Written only if absent, so rerunning never overwrites a working config or
# rotates the JWT secret out from under sessions that are already live.

$EnvFile = Join-Path $Backend '.env'
if (Test-Path $EnvFile) {
    Good ".env already exists, left alone"
} else {
    Say "writing .env"
    # A real random secret. The default in config.py is 'dev-only-change-me',
    # and every access token in the system is signed with this.
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $jwt = [Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', ''

    $lines = @(
        'ENV=development',
        '',
        '# Two roles against one database. The second owns nothing, so row-level',
        '# security applies to it. Without it the API connects as the owner and',
        '# every studio can read every other studio.',
        'DATABASE_URL=postgresql+psycopg://frame:frame@localhost:55432/frame',
        'DATABASE_APP_URL=postgresql+psycopg://frame_app:frame_app@localhost:55432/frame',
        '',
        'STORAGE_BACKEND=local',
        'LOCAL_STORAGE_DIR=./storage',
        '',
        '# The permanent tunnel hostname. Thumbnail and upload URLs are built',
        '# from PUBLIC_BASE_URL, so if it is wrong the gallery loads with every',
        '# image broken.',
        "PUBLIC_BASE_URL=$Hostname",
        "GUEST_BASE_URL=$Hostname",
        "CORS_ORIGINS_RAW=$Hostname,http://localhost:8000,file://,null",
        '',
        '# Measured, not guessed. The impostor ceiling on real photographs was',
        '# 0.424, so this clears it with room to spare.',
        'MATCH_THRESHOLD=0.60',
        'MIN_FACE_PX=112',
        'MIN_DETECT_SCORE=0.7',
        'INDEX_MIN_BLUR=0.0',
        'FACE_BACKEND=auraface',
        '',
        "JWT_SECRET=$jwt",
        '',
        '# Watermark, lower-left of every photograph. Empty = off. Applied at',
        '# index time, so set it BEFORE uploading, not after.',
        'WATERMARK_TEXT=',
        'WATERMARK_OPACITY=0.75'
    )
    Set-Content -Path $EnvFile -Value $lines -Encoding utf8
    Good ".env written with a fresh JWT secret"
}

# ── 6. database ───────────────────────────────────────────────────────

Say "starting postgres"
Push-Location $Backend
docker compose up -d
$dbUp = $?
Pop-Location
if (-not $dbUp) { Bad "docker compose failed"; exit 1 }

Say "waiting for postgres to accept connections"
$ready = $false
foreach ($i in 1..60) {
    if ((Quiet 'docker' @('exec', 'frame-db', 'pg_isready', '-U', 'frame')) -eq 0) {
        $ready = $true; break
    }
    Start-Sleep -Seconds 2
}
if (-not $ready) { Bad "postgres did not come up within 2 minutes"; exit 1 }
Good "postgres is up"

Say "running migrations"
Push-Location $Backend
python -m alembic upgrade head
$migOk = $?
Pop-Location
if (-not $migOk) { Bad "migrations failed"; exit 1 }
Good "schema is current"

# ── 7. the guest app ──────────────────────────────────────────────────
# Built, not dev-served. The built app calls whatever host served it, which is
# how the QR link and the API end up on one origin. A dev server would
# reintroduce a second origin and, with no VITE_API_BASE, would silently run
# mock handlers and never contact the backend at all.

Say "building the guest app"
Push-Location $Root
npm run build --workspace web
$buildOk = $?
Pop-Location
if (-not $buildOk) { Bad "web build failed"; exit 1 }
Good "guest app built"

# ── 8. tunnel credentials ─────────────────────────────────────────────

$CfDir = Join-Path $env:USERPROFILE '.cloudflared'
$haveCf = (Test-Path (Join-Path $CfDir 'cert.pem')) -and (Test-Path (Join-Path $CfDir 'config.yml'))
if ($haveCf) {
    Good "cloudflared credentials present"
} else {
    Warn "cloudflared credentials are NOT on this machine yet."
    Say  "Copy these three files from the other laptop's .cloudflared folder into:"
    Say  "    $CfDir"
    Say  "      cert.pem"
    Say  "      dab4c09b-c8fb-4a8b-af30-f111e86562ac.json"
    Say  "      config.yml"
    Say  "Then edit config.yml so credentials-file points at THIS machine's path."
}

# ── 9. the admin account ──────────────────────────────────────────────

Write-Host ""
Say "creating the platform administrator"
Say "This prints a password ONCE. Write it down before closing the window."
Write-Host ""
Push-Location $Backend
python scripts/create_admin.py admin
Pop-Location

Write-Host ""
Write-Host "  Done." -ForegroundColor Cyan
Write-Host ""
Say "To run it, in two windows:"
Write-Host "    cd $Backend;  python -u -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
Write-Host "    cd $Backend;  python -u -m app.worker"
Say "and a third for the tunnel:"
Write-Host "    cloudflared tunnel run frame"
Write-Host ""
Say "Then check $Hostname/v1/health responds before doing anything else."
Write-Host ""
