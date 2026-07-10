param(
    [switch]$SkipRedshift,
    [switch]$ContinueOnRedshiftFail,
    [switch]$SkipInstall,
    [switch]$NoDashboard,
    [string]$DashboardPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $Root

function Write-Title {
    param([string]$Text, [string]$Color = "Cyan")
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor $Color
    Write-Host " $Text" -ForegroundColor $Color
    Write-Host "==============================================" -ForegroundColor $Color
}

function Resolve-PythonCommand {
    $candidates = @("python", "py")
    foreach ($cmd in $candidates) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) { return $cmd }
    }
    throw "No se encontró Python en PATH. Instala Python o activa tu entorno virtual manualmente."
}

function Use-ProjectEnv {
    $envCandidates = @(
        (Join-Path $Root ".venv\Scripts\Activate.ps1"),
        (Join-Path $Root "env\Scripts\Activate.ps1")
    )

    foreach ($activate in $envCandidates) {
        if (Test-Path $activate) {
            Write-Host "Activando entorno: $activate" -ForegroundColor Green
            . $activate
            return
        }
    }

    Write-Host "No se encontró .venv/env. Creando .venv..." -ForegroundColor Yellow
    $py = Resolve-PythonCommand
    & $py -m venv (Join-Path $Root ".venv")
    $newActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"

    if (-not (Test-Path $newActivate)) {
        throw "No se pudo crear el entorno virtual en .venv"
    }

    . $newActivate
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$StepName,
        [Parameter(Mandatory = $true)][scriptblock]$Command,
        [switch]$ContinueOnFail
    )

    Write-Host ""
    Write-Host $StepName -ForegroundColor Cyan

    try {
        & $Command
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }

        if ($exitCode -ne 0) {
            throw "ExitCode=$exitCode"
        }

        Write-Host "OK: $StepName" -ForegroundColor Green
    }
    catch {
        if ($ContinueOnFail) {
            Write-Host "ADVERTENCIA: Falló el paso '$StepName', pero se continúa. Detalle: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        else {
            throw "Falló el paso: $StepName. Detalle: $($_.Exception.Message)"
        }
    }
}

function Install-Dependencies {
    if ($SkipInstall) {
        Write-Host "Saltando instalación de dependencias por parámetro -SkipInstall" -ForegroundColor Yellow
        return
    }

    Invoke-NativeStep "Validando pip..." {
        python -m pip install --upgrade pip
    }

    if (Test-Path (Join-Path $Root "requirements.txt")) {
        Invoke-NativeStep "Instalando dependencias desde requirements.txt..." {
            python -m pip install -r (Join-Path $Root "requirements.txt")
        }
    }
    else {
        Invoke-NativeStep "Instalando dependencias base..." {
            python -m pip install pandas numpy pyarrow openpyxl python-dotenv sqlalchemy psycopg2-binary rapidfuzz scikit-learn pyyaml streamlit plotly
        }
    }
}

function Resolve-DashboardPath {
    param([string]$RequestedPath)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $candidate = if ([System.IO.Path]::IsPathRooted($RequestedPath)) {
            $RequestedPath
        }
        else {
            Join-Path $Root $RequestedPath
        }

        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
        throw "El DashboardPath indicado no existe: $candidate"
    }

    $candidates = @(
        "ventas_dashboard\run_app_ventas_dashboard.py",
        "ventas_dashboard\app_ventas_dashboard.py",
        "app_ventas_dashboard.py",
        "run_app_ventas_dashboard.py",
        "dashboard\app.py",
        "app.py"
    ) | ForEach-Object { Join-Path $Root $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }

    $found = Get-ChildItem $Root -Recurse -File -Filter "*.py" |
        Where-Object { $_.Name -match "dashboard|streamlit|app" } |
        Select-Object -First 1

    if ($found) { return $found.FullName }

    Write-Host "No se encontró un archivo de dashboard automáticamente." -ForegroundColor Red
    Write-Host "Archivos .py detectados en el proyecto:" -ForegroundColor Yellow
    Get-ChildItem $Root -Recurse -File -Filter "*.py" |
        Select-Object FullName |
        Format-Table -AutoSize

    throw "No se encontró el dashboard. Pásalo explícitamente con -DashboardPath 'ruta\archivo.py'."
}

function Open-StreamlitDashboard {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "No existe el archivo Streamlit: $Path"
    }

    Write-Host "Dashboard detectado: $Path" -ForegroundColor Green

    $activatePath = Join-Path $Root ".venv\Scripts\Activate.ps1"
    $dashboardEscaped = $Path.Replace("'", "''")
    $rootEscaped = $Root.Replace("'", "''")
    $activateEscaped = $activatePath.Replace("'", "''")

    if (Test-Path $activatePath) {
        $command = "Set-Location '$rootEscaped'; . '$activateEscaped'; python -m streamlit run '$dashboardEscaped'"
    }
    else {
        $command = "Set-Location '$rootEscaped'; python -m streamlit run '$dashboardEscaped'"
    }

    Start-Process powershell -ArgumentList @("-NoExit", "-Command", $command)
    Write-Host "Streamlit se abrió en otra ventana para no bloquear el pipeline." -ForegroundColor Green
}

Write-Title "PROPIETARIOS"
Write-Host "Raíz del proyecto: $Root" -ForegroundColor DarkCyan

Use-ProjectEnv
Install-Dependencies

if (-not $SkipRedshift) {
    Invoke-NativeStep "0/11 Extrayendo Redshift con cache diario..." {
        python (Join-Path $Root "tools\extract_redshift_daily.py")
    } -ContinueOnFail:$ContinueOnRedshiftFail
}
else {
    Write-Host "0/11 Saltando Redshift. Usando parquets locales existentes..." -ForegroundColor Yellow
}

Invoke-NativeStep "1/11 Ejecutando Ventas..." {
    python (Join-Path $Root "ventas_por_cobrar\main_pipeline.py")
}

if (-not $NoDashboard) {
    Invoke-NativeStep "2/11 Abriendo Dashboard Ventas..." {
        $resolvedDashboardPath = Resolve-DashboardPath -RequestedPath $DashboardPath
        Open-StreamlitDashboard -Path $resolvedDashboardPath
    }
}
else {
    Write-Host "2/11 Saltando dashboard por parámetro -NoDashboard" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Listo. Revisa la carpeta outputs" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green

Write-Title "PIPELINE COMPLETADO" "Green"
Write-Host "Output matriz: data\gold\power_bi\matriz_venta_cobranza" -ForegroundColor Green
Write-Host ""
