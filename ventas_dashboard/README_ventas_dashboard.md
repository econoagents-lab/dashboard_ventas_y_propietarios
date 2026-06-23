# Dashboard Ventas por Proyecto

Mini app Streamlit para visualizar las ventas por proyecto desde tus parquets locales y descargar el Excel aesthetic con el mismo formato del pipeline OOP.

## Archivos

- `app_ventas_dashboard.py`: interfaz/dashboard.
- `build_ventas_por_proyecto_excel_oop.py`: pipeline OOP configurable.
- `requirements_ventas_dashboard.txt`: dependencias.

## Estructura esperada

```text
project_root/
├─ app_ventas_dashboard.py
├─ build_ventas_por_proyecto_excel_oop.py
├─ requirements_ventas_dashboard.txt
└─ data/
   └─ raw/
      ├─ procesos.parquet
      ├─ clientes.parquet
      ├─ proyectos.parquet
      └─ unidades.parquet
```

## Instalación

```bash
pip install -r requirements_ventas_dashboard.txt
```

## Ejecutar

```bash
streamlit run app_ventas_dashboard.py
```

## Uso

1. Indica la carpeta raw, por defecto `data/raw`.
2. Selecciona columnas base, opcionales o calculadas.
3. Filtra por proyecto, tipo de unidad, comprador o DNI.
4. Descarga el Excel aesthetic respetando filtros y columnas activas.


## Ajustes incorporados

- `num_unidad` conserva `unidades.nombre` completo cuando `tipo_unidad` corresponde a departamento, flat, dúplex o tríplex. Ejemplo: `A1201` se mantiene como `A1201`.
- Para estacionamientos, depósitos y otros adicionales, `num_unidad` sigue extrayendo solo los dígitos con regex.
- Se agregó `precio_lista_al_comprar`, tomado desde `unidades.precio_base_proforma`.
- La columna raw `precio_base_proforma` queda disponible como opcional si quieres auditar el origen.
