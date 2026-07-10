# Dashboard Medallion — Propietarios + Stock Completo

Nueva versión end-to-end sin `leads_crm.xlsx`.

Esta versión construye dos universos:

1. **Propietarios / ventas**  
   Universo actual: solo unidades que tienen proceso de venta.

2. **Stock completo**  
   Universo ampliado: todas las unidades desde `unidades.parquet`, tengan o no propietario.  
   Luego se hace un `left merge` con el universo de propietarios para traer comprador, DNI, copropietarios y precio de venta.

## Estructura

```text
ventas_medallion_stock_pack/
├─ app_ventas_dashboard_stock.py
├─ requirements.txt
├─ README.md
├─ scripts/
│  └─ medallion_stock_pipeline.py
└─ data/
   ├─ raw/
   │  ├─ procesos.parquet
   │  ├─ clientes.parquet
   │  ├─ proyectos.parquet
   │  └─ unidades.parquet
   ├─ bronze/
   ├─ silver/
   ├─ gold/
   └─ exports/
```

## Capa Bronze

Lee y guarda snapshots desde:

```text
data/raw/procesos.parquet
data/raw/clientes.parquet
data/raw/proyectos.parquet
data/raw/unidades.parquet
```

## Capa Silver

Genera:

```text
silver_unidades.parquet
silver_proyectos.parquet
silver_clientes.parquet
silver_separaciones.parquet
silver_ventas.parquet
silver_copropietarios.parquet
```

## Capa Gold

Genera:

```text
data/gold/mart_propietarios_ventas.parquet
data/gold/mart_stock_unidades_completo.parquet
```

## Columnas finales de Stock Completo

El output final sigue la estructura del archivo de referencia:

```text
Proyecto
Torre
nombre
tipo de unidad
codigo
estado comercial
Estado comercial
comprador
dni comprador
copropietario 1
dni coprop 1
copropietario 2
dni coprop 2
copropietario 3
dni coprop 3
precio lista al comprar
precio venta
Precio de lista Actual
Descuento actual
Monto actual dscto
```

## Reglas clave

### Universo maestro

El universo maestro del stock completo es `unidades.parquet`.

### Merge

Se hace:

```text
unidades LEFT JOIN propietarios/ventas
ON unidades.codigo = propietarios.codigo
```

Si no existe propietario:

```text
comprador = vacío
dni comprador = vacío
copropietarios = vacío
precio venta = vacío
Estado comercial = Disponible
```

Si existe propietario o proceso de venta:

```text
Estado comercial = Vendido
```

### Letras iniciales de unidades

La columna `nombre` conserva el valor original de `unidades.nombre`:

```text
E1     -> E1
D-1201 -> D-1201
AX05   -> AX05
```

## Instalar

```bash
pip install -r requirements.txt
```

## Construir capas + Excel por consola

```bash
python scripts/medallion_stock_pipeline.py --build_all --export_excel
```

## Ejecutar dashboard

```bash
streamlit run app_ventas_dashboard_stock.py
```

## Qué incluye la app

La app tiene dos pestañas:

1. **Stock completo**
   - Todas las unidades.
   - Vendidas y disponibles.
   - Propietarios vacíos cuando no hay venta.
   - KPIs de stock, disponibles, vendidas, valor lista actual y valor disponible.

2. **Propietarios / ventas**
   - Solo universo vendido.
   - Compradores, DNI, copropietarios y precio de venta.

Además, permite descargar un Excel aesthetic con:

```text
00_RESUMEN_STOCK
01_STOCK_COMPLETO
02_PROPIETARIOS
1 hoja por proyecto
```

## Runner PowerShell adaptado

Esta versión incluye `run_all_sales.ps1`, adaptado para ejecutar el flujo completo:

```powershell
.\run_all_sales.ps1
```

Modo solo construir capas y Excel, sin abrir dashboard:

```powershell
.\run_all_sales.ps1 -NoDashboard
```

Modo sin extraer Redshift, usando parquets existentes:

```powershell
.\run_all_sales.ps1 -SkipRedshift
```

Modo sin Excel, solo gold marts:

```powershell
.\run_all_sales.ps1 -NoExcel
```

El runner llama internamente a:

```powershell
python scripts\medallion_stock_pipeline.py --build_all --export_excel
streamlit run app_ventas_dashboard_stock.py
```

## Nota V4: si no ves unidades disponibles

La tabla de propietarios/ventas solo muestra vendidas. Para ver libres debes entrar en la pestaña:

```text
📦 Stock completo
```

La app V4 incluye un diagnóstico de universo cargado:

- Filas en stock gold
- Filas con propietarios
- Diferencia stock - propietarios
- Distribución de Estado comercial

Si `Filas en stock gold` es igual a `Filas con propietarios`, entonces `data/raw/unidades.parquet` no está trayendo el universo completo de unidades, sino solo unidades vendidas/procesadas. En ese caso hay que corregir la extracción Redshift de `unidades` para que no filtre por procesos/ventas.

También se agregaron alias de compatibilidad:

```text
app_ventas_dashboard_stock.py       # app principal correcta
app_ventas_dashboard_medallion.py   # alias hacia stock
app_ventas_dashboard.py             # alias hacia stock
```

Así, aunque ejecutes el nombre antiguo, se abre el dashboard de stock completo.

## V5 - Corrección de calidad de columnas

Esta versión corrige problemas típicos del output `stock_unidades_completo`:

- Evita que columnas de texto salgan como `None`, `<NA>` o `nan`; ahora quedan vacías.
- `Descuento actual` y `Monto actual dscto` se toman desde `unidades` cuando existen. Solo se calcula fallback cuando la fuente no trae esos campos.
- `precio lista al comprar` prioriza `precio_base_proforma` desde `unidades`.
- Se elimina una columna duplicada `codigo_proforma` del mart de propietarios.
- Se agrega diagnóstico con:

```bash
python scripts/check_stock_output_quality.py --gold_path data/gold/mart_stock_unidades_completo.parquet
```

Después de reemplazar archivos, reconstruye todo:

```powershell
.\run_all_sales.ps1 -SkipRedshift
```

## V6 - Corrección de precios y descuentos

Esta versión corrige las columnas monetarias del archivo `stock_unidades_completo.xlsx`:

- `precio lista al comprar`
- `precio venta`
- `Precio de lista Actual`
- `Descuento actual`
- `Monto actual dscto`

### Regla corregida

`Descuento actual` y `Monto actual dscto` pertenecen al universo actual de `unidades`; no deben calcularse como diferencia entre precio de lista y precio de venta.

La prioridad aplicada es:

1. Tomar columnas reales de `unidades` cuando existan.
2. Si falta `% descuento` pero existe `monto_actual_dscto`, calcular `% = monto / precio_de_lista_actual`.
3. Si faltan ambas, aplicar fallback de negocio: departamentos `8%`, otros tipos `0%`.
4. Calcular `monto_actual_dscto = precio_de_lista_actual * descuento_actual`.

### Diagnóstico

```powershell
python scripts/check_stock_output_quality.py --gold_path data/gold/mart_stock_unidades_completo.parquet
```
