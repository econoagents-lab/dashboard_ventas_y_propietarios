param(
    [switch]$SkipRedshift,
    [switch]$ContinueOnRedshiftFail
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " PROPIETARIOS " -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Use-ProjectEnv {
    if (Test-Path ".\env\Scripts\Activate.ps1") {
        Write-Host "Activando env existente: env" -ForegroundColor Green
        . ".\env\Scripts\Activate.ps1"
    }
    elseif (Test-Path ".\.venv\Scripts\Activate.ps1") {
        Write-Host "Activando env existente: .venv" -ForegroundColor Green
        . ".\.venv\Scripts\Activate.ps1"
    }
    else {
        Write-Host "No se encontró env/.venv. Creando .venv..." -ForegroundColor Yellow
        python -m venv .venv
        . ".\.venv\Scripts\Activate.ps1"
    }
}

function Invoke-Step {
    param(
        [string]$StepName,
        [scriptblock]$Command
    )

    Write-Host ""
    Write-Host $StepName -ForegroundColor Cyan
    & $Command

    if ($LASTEXITCODE -ne 0) {
        throw "Falló el paso: $StepName"
    }
}

Use-ProjectEnv

Write-Host "Instalando / validando dependencias..." -ForegroundColor Cyan
python -m pip install --upgrade pip
<# if (Test-Path ".\requirements.txt") {
    pip install -r requirements.txt
}
else {
    pip install pandas numpy pyarrow openpyxl python-dotenv sqlalchemy psycopg2-binary rapidfuzz scikit-learn pyyaml
} #>

if (Test-Path ".\requirements.txt") {
    python -m pip install -r requirements.txt
}
else {
    python -m pip install pandas numpy pyarrow openpyxl python-dotenv sqlalchemy psycopg2-binary rapidfuzz scikit-learn pyyaml streamlit plotly
}

if (-not $SkipRedshift) {
    try {
        Invoke-Step "0/11 Extrayendo Redshift con cache diario..." {
            python .\tools\extract_redshift_daily.py
        }
    }
    catch {
        if ($ContinueOnRedshiftFail) {
            Write-Host "Redshift falló, pero continúo con parquets locales existentes." -ForegroundColor Yellow
        }
        else {
            throw
        }
    }
}
else {
    Write-Host "0/11 Saltando Redshift. Usando parquets locales existentes..." -ForegroundColor Yellow
}

Invoke-Step "1/ Ejecutando Ventas..." {
    python .\ventas_por_cobrar\main_pipeline.py
}

Invoke-Step "2.0/11 Abriendo Dashboard Ventas..." {
    $DashboardPath = Join-Path $PSScriptRoot "ventas_dashboard\app_ventas_dashboard.py"

    if (!(Test-Path $DashboardPath)) {
        throw "No se encontró el dashboard en: $DashboardPath"
    }

    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "cd '$PSScriptRoot'; . .\.venv\Scripts\Activate.ps1; python -m streamlit run '$DashboardPath'"
    )
}
<# Invoke-Step "2.0/11 Ejecutando Ingresos Cobrados..." {
    
    $DashboardPath = Join-Path $PSScriptRoot "ventas_dashboard\app_ventas_dashboard.py"
    python -m streamlit run $DashboardPath
    #streamlit run $DashboardPath
}
 #>

 
$ErrorActionPreference = "Stop"

Write-Host "=========================================="
Write-Host "Ejecutando validaciones Marketing / CRM"
Write-Host "=========================================="

#if (!(Test-Path "data\raw\clientes_proyectos.xlsx")) {
<# if (!(Test-Path "data\raw\clientes_proyectos.parquet")) {

    Write-Host "ERROR: No se encontro data\raw\clientes_proyectos"
    Write-Host "Coloca tu export del CRM/Formularios en la carpeta data y renombralo como leads_crm.xlsx"
    exit 1
} #>

<# Invoke-Step "3/Marketing..." {
       
    $AuditFormularios = Join-Path $PSScriptRoot "validaciones_marketing_crm\audit_leads_formularios.py"
    python $AuditFormularios
}

Invoke-Step "4/Marketing..." {
       
    $BDnegativa = Join-Path $PSScriptRoot "validaciones_marketing_crm\generar_bbdd_negativa.py"
    python $BDnegativa
} #>
<# 
Write-Host "1/2 Auditoria leads formularios..."
python audit_leads_formularios.py

Write-Host "2/2 BBDD negativa exclusion..."
python generar_bbdd_negativa.py
#>
Write-Host "=========================================="
Write-Host "Listo. Revisa la carpeta outputs"
Write-Host "=========================================="



    
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host " PIPELINE COMPLETADO" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "Output matriz:" -ForegroundColor Green
Write-Host "data\gold\power_bi\matriz_venta_cobranza" -ForegroundColor Green
Write-Host ""