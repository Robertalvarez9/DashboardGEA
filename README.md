# Dashboard de productos terminados y despachos

Dashboard web de solo lectura para consultar inventario de Producto Terminado (PT), despachos e indicadores gerenciales desde Google Sheets. Este modulo no lee ni modifica el Excel maestro del programa GEA.

## Instalacion

Desde la carpeta raiz del programa:

```powershell
cd dashboard_pt
pip install -r requirements.txt
```

## Ejecucion local

```powershell
streamlit run app.py
```

La aplicacion pedira una contrasena antes de mostrar la informacion.

Para abrirlo desde otra computadora en la misma red local, usa:

```powershell
ejecutar_dashboard_pt_red_local.bat
```

Para abrirlo desde cualquier red o desde internet, revisa `PUBLICAR_EN_INTERNET.md`.

## Sincronizacion con Google Sheets

El dashboard lee datos desde Google Sheets. Para actualizar esas hojas desde el Excel local, ejecuta:

```powershell
python sync_google_sheets.py
```

Tambien puedes usar el archivo de esta carpeta:

```powershell
sincronizar_dashboard_pt.bat
```

El sincronizador lee `../Base de datos GEA.xlsx` en modo solo lectura y actualiza estas hojas del Google Sheets:

- `Inventario_PT`
- `Despachos`

Para validar sin escribir en Google Sheets:

```powershell
python sync_google_sheets.py --dry-run
```

Para automatizarlo, crea una tarea en el Programador de tareas de Windows que ejecute periodicamente:

```powershell
python "C:\Users\suppl\Desktop\Programa GEA V2.1\dashboard_pt\sync_google_sheets.py"
```

## Configuracion de secretos

1. Copia `.streamlit/secrets.example.toml` como `.streamlit/secrets.toml`.
2. Cambia `APP_PASSWORD` por la contrasena real de acceso.
3. Completa los datos de la service account de Google Cloud.
4. Comparte el Google Sheets privado con el correo `client_email` de la service account con permiso Viewer/Lector.

El archivo `.streamlit/secrets.toml` contiene credenciales reales y no debe subirse a GitHub.

## Estructura del Google Sheets

El Google Sheets debe tener dos hojas.

### Inventario_PT

Columnas requeridas:

- `Producto`
- `Presentacion`
- `Stock actual`
- `Total kg`
- `Stock minimo`
- `Estado`
- `Ultima actualizacion`

### Despachos

Columnas requeridas:

- `Fecha`
- `Fecha y hora`
- `Numero despacho`
- `Cliente`
- `Producto`
- `Presentacion`
- `Cantidad`
- `Total kg`
- `Responsable`
- `Observaciones`

## Despliegue en Streamlit Community Cloud

1. Sube la carpeta `dashboard_pt` al repositorio sin incluir `.streamlit/secrets.toml`.
2. En Streamlit Community Cloud, crea una nueva app apuntando a `dashboard_pt/app.py`.
3. En la configuracion de secretos de Streamlit Cloud, pega el contenido real de `.streamlit/secrets.toml`.
4. Verifica que el Google Sheets este compartido con la service account en modo Viewer/Lector.
5. Despliega la app.

## Archivos que no deben subirse

- `.streamlit/secrets.toml`
- `.env`
- Carpetas de entorno virtual como `.venv/` o `venv/`
- Archivos temporales o cache de Python

## Seguridad

- El dashboard es solo lectura.
- No contiene botones para agregar, editar ni eliminar registros.
- La contrasena se valida con `hmac.compare_digest`.
- Las credenciales y la contrasena se leen desde `st.secrets`.
- No se expone ni se lee el Excel maestro del programa GEA.
