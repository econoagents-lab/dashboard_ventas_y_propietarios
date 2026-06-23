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
if (Test-Path ".\requirements.txt") {
    pip install -r requirements.txt
}
else {
    pip install pandas numpy pyarrow openpyxl python-dotenv sqlalchemy psycopg2-binary rapidfuzz scikit-learn pyyaml
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

Invoke-Step "2.0/11 Ejecutando Ingresos Cobrados..." {
    
    #streamlit run ".\ventas_dashboard\run app_ventas_dashboard.py"
    $DashboardPath = Join-Path $PSScriptRoot "ventas_dashboard\app_ventas_dashboard.py"
    streamlit run $DashboardPath
    #python .\ventas_dashboard\streamlit run app_ventas_dashboard.py
}
    <#  Output dir: C:\Users\user\Documents\data_science_and_analytics\ventas_con_cobranzas\real_state_cobranzas\real_estate_revenue_match\data\bronze
        Master: C:\Users\user\Documents\data_science_and_analytics\ventas_con_cobranzas\real_state_cobranzas\real_estate_revenue_match\data\bronze\tablon_MASTER.parquet
        Data Quality Report: C:\Users\user\Documents\data_science_and_analytics\ventas_con_cobranzas\real_state_cobranzas\real_estate_revenue_match\data\bronze\data_quality_report.csv #>

<# Invoke-Step "2.1/11 Enriqueciendo los tabloens de Excel cruzando por cada comprobante lo que está en EFAC (TABLON_Excel con Maestros)..." {
    .\2_1_merge_efac_tablones\scripts\run_merge_efac_tablones.ps1 `
        -TablonDir "data\bronze" `
        -EfacDir "data\raw\pagos_de_efac" `
        -Overwrite
} #>
    <# Genera el tablon master #>
    <# Invoke-Step "3/11 Ejecutando Finanzas y Pagos..." {
        python .\2_1_finanzas_pagos\main_pipeline.py} #>


# Insertar DESPUES del paso que genera data\bronze\tablon_*.parquet
# y ANTES de 2_1_finanzas_pagos / pagos_eventos.parquet.
# Este paso reconstruye tablon_MASTER despues de enriquecer los tablones individuales.


Write-Host "Validando patch EFAC priority en cobranza merge..." -ForegroundColor Cyan
$MergeScript = ".\3_merge_venta_recibido\cobranza_pipeline.py"
$MergeContent = Get-Content $MergeScript -Raw
if ($MergeContent -notmatch "add_efac_priority_columns_to_pagos_eventos") {
    Write-Host "ADVERTENCIA: No detecto patch EFAC priority en $MergeScript" -ForegroundColor Yellow
} else {
    Write-Host "OK: patch EFAC priority detectado." -ForegroundColor Green
}


Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host " PIPELINE COMPLETADO" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "Output matriz:" -ForegroundColor Green
Write-Host "data\gold\power_bi\matriz_venta_cobranza" -ForegroundColor Green
Write-Host ""
