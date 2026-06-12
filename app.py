from __future__ import annotations

import hmac
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from streamlit_gsheets import GSheetsConnection

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # pragma: no cover - only used when dependency is missing locally.
    st_autorefresh = None


INVENTORY_SHEET = "Inventario_PT"
DISPATCH_SHEET = "Despachos"

INVENTORY_COLUMNS = [
    "Producto",
    "Presentacion",
    "Stock actual",
    "Stock minimo",
    "Estado",
    "Ultima actualizacion",
]

DISPATCH_COLUMNS = [
    "Fecha",
    "Hora",
    "Numero despacho",
    "Cliente",
    "Producto",
    "Presentacion",
    "Cantidad",
    "Estado",
    "Responsable",
]

CRITICAL_STATES = ["Bajo stock", "Sin stock"]


st.set_page_config(
    page_title="Dashboard PT - GEA",
    page_icon="📦",
    layout="wide",
)


def apply_style() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.6rem;
            padding-bottom: 2rem;
        }
        div[data-testid="stMetric"] {
            background: #f7f8fa;
            border: 1px solid #e3e7ee;
            border-radius: 8px;
            padding: 14px 16px;
        }
        div[data-testid="stMetric"] label {
            color: #445064;
        }
        .gea-muted {
            color: #5e6a7d;
            font-size: 0.92rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def check_password() -> bool:
    try:
        expected_password = st.secrets.get("APP_PASSWORD")
        if expected_password is None:
            expected_password = st.secrets.get("\ufeffAPP_PASSWORD")
    except StreamlitSecretNotFoundError:
        expected_password = None

    if not expected_password:
        st.error("Falta configurar APP_PASSWORD en .streamlit/secrets.toml.")
        st.info("Usa .streamlit/secrets.example.toml como plantilla y no subas secrets.toml a GitHub.")
        return False

    if "password_ok" not in st.session_state:
        st.session_state.password_ok = False

    if st.session_state.password_ok:
        return True

    with st.form("login_form"):
        st.subheader("Acceso restringido")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar")

    if submitted:
        if hmac.compare_digest(password, str(expected_password)):
            st.session_state.password_ok = True
            st.rerun()

        st.error("Contraseña incorrecta.")

    return False


def validate_columns(df: pd.DataFrame, required_columns: list[str], sheet_name: str) -> list[str]:
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        st.error(
            f"La hoja {sheet_name} no tiene las columnas requeridas: "
            f"{', '.join(missing_columns)}."
        )
    return missing_columns


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def format_connection_error(exc: Exception) -> str:
    messages = [str(exc).strip()]

    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        messages.append(str(cause).strip())

    context = getattr(exc, "__context__", None)
    if context is not None:
        messages.append(str(context).strip())

    combined = " ".join(message for message in messages if message)

    if "sheets.googleapis.com" in combined or "SERVICE_DISABLED" in combined:
        return (
            "La Google Sheets API esta deshabilitada en el proyecto de Google Cloud "
            "de la service account. Activa Google Sheets API y espera unos minutos "
            "antes de volver a ejecutar el dashboard."
        )

    if "PERMISSION_DENIED" in combined or isinstance(exc, PermissionError):
        return (
            "Google nego el acceso al Sheets. Verifica que el archivo este compartido "
            "con el correo de la service account y que la Google Sheets API este activa."
        )

    return combined or type(exc).__name__


def get_gsheets_config_issue() -> str | None:
    try:
        connections = st.secrets.get("connections", {})
    except StreamlitSecretNotFoundError:
        return "Falta configurar la sección [connections.gsheets] en .streamlit/secrets.toml."

    gsheets_config = connections.get("gsheets", {}) if hasattr(connections, "get") else {}
    required_keys = [
        "spreadsheet",
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "auth_uri",
        "token_uri",
        "auth_provider_x509_cert_url",
        "client_x509_cert_url",
    ]

    missing_keys = [
        key
        for key in required_keys
        if not str(gsheets_config.get(key, "")).strip()
    ]
    if missing_keys:
        return (
            "Faltan estos datos en [connections.gsheets]: "
            f"{', '.join(missing_keys)}."
        )

    placeholder_keys = [
        key
        for key in required_keys
        if "xxx" in str(gsheets_config.get(key, "")).lower()
        or str(gsheets_config.get(key, "")).strip() == "URL_DEL_GOOGLE_SHEETS"
    ]
    if placeholder_keys:
        return (
            "La conexión de Google Sheets todavía tiene valores de ejemplo. "
            f"Reemplaza: {', '.join(placeholder_keys)}."
        )

    private_key = str(gsheets_config.get("private_key", ""))
    if "BEGIN PRIVATE KEY" not in private_key or "END PRIVATE KEY" not in private_key:
        return "La llave private_key no parece ser una llave PEM válida de service account."

    return None


def _read_sheet(conn: GSheetsConnection, worksheet: str, columns: list[str]) -> pd.DataFrame:
    try:
        data = conn.read(worksheet=worksheet, ttl=0)
    except Exception as exc:  # noqa: BLE001 - shown as operational feedback to the manager/admin.
        st.error(f"No se pudo leer la hoja {worksheet}: {format_connection_error(exc)}")
        return _empty_frame(columns)

    if data is None:
        return _empty_frame(columns)

    df = pd.DataFrame(data)
    df.columns = [str(column).strip() for column in df.columns]
    df = df.dropna(how="all")

    if df.empty and len(df.columns) == 0:
        return _empty_frame(columns)

    return df


@st.cache_data(ttl=55, show_spinner="Consultando Google Sheets...")
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, datetime]:
    config_issue = get_gsheets_config_issue()
    if config_issue:
        st.warning(config_issue)
        st.info("Copia los datos reales de la service account en .streamlit/secrets.toml.")
        return _empty_frame(INVENTORY_COLUMNS), _empty_frame(DISPATCH_COLUMNS), datetime.now()

    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
    except Exception as exc:  # noqa: BLE001 - keeps configuration errors user-friendly.
        st.error(f"No se pudo crear la conexión a Google Sheets: {format_connection_error(exc)}")
        return _empty_frame(INVENTORY_COLUMNS), _empty_frame(DISPATCH_COLUMNS), datetime.now()

    inventory_df = _read_sheet(conn, INVENTORY_SHEET, INVENTORY_COLUMNS)
    dispatch_df = _read_sheet(conn, DISPATCH_SHEET, DISPATCH_COLUMNS)

    return inventory_df, dispatch_df, datetime.now()


def clean_inventory_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_frame(INVENTORY_COLUMNS)

    cleaned = df.copy()

    for column in INVENTORY_COLUMNS:
        if column not in cleaned.columns:
            cleaned[column] = pd.NA

    cleaned = cleaned[INVENTORY_COLUMNS]
    cleaned["Producto"] = cleaned["Producto"].astype("string").str.strip()
    cleaned["Presentacion"] = cleaned["Presentacion"].astype("string").str.strip()
    cleaned["Estado"] = cleaned["Estado"].astype("string").str.strip()
    cleaned["Stock actual"] = pd.to_numeric(cleaned["Stock actual"], errors="coerce").fillna(0)
    cleaned["Stock minimo"] = pd.to_numeric(cleaned["Stock minimo"], errors="coerce").fillna(0)
    cleaned["Ultima actualizacion"] = parse_date_series(cleaned["Ultima actualizacion"])

    return cleaned


def clean_dispatch_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_frame(DISPATCH_COLUMNS)

    cleaned = df.copy()

    for column in DISPATCH_COLUMNS:
        if column not in cleaned.columns:
            cleaned[column] = pd.NA

    cleaned = cleaned[DISPATCH_COLUMNS]
    text_columns = ["Numero despacho", "Cliente", "Producto", "Presentacion", "Estado", "Responsable"]
    for column in text_columns:
        cleaned[column] = cleaned[column].astype("string").str.strip()

    cleaned["Fecha"] = parse_date_series(cleaned["Fecha"])
    cleaned["Cantidad"] = pd.to_numeric(cleaned["Cantidad"], errors="coerce").fillna(0)

    return cleaned


def _select_options(df: pd.DataFrame, column: str) -> list[str]:
    if df.empty or column not in df.columns:
        return []

    values = (
        df[column]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .sort_values()
        .unique()
        .tolist()
    )
    return values


def _filter_by_multiselect(
    df: pd.DataFrame,
    column: str,
    selected_values: list[str],
) -> pd.DataFrame:
    if not selected_values or df.empty or column not in df.columns:
        return df
    return df[df[column].astype(str).isin(selected_values)]


def parse_date_series(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip().replace("", pd.NA)
    iso_mask = text.str.match(r"^\d{4}-\d{1,2}-\d{1,2}", na=False)
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    parsed.loc[iso_mask] = pd.to_datetime(
        text.loc[iso_mask],
        errors="coerce",
        dayfirst=False,
        yearfirst=True,
    )
    parsed.loc[~iso_mask] = pd.to_datetime(
        text.loc[~iso_mask],
        errors="coerce",
        dayfirst=True,
    )

    return parsed


def _plot_empty(message: str) -> None:
    st.info(message)


def render_kpis(inventory_df: pd.DataFrame, dispatch_df: pd.DataFrame) -> None:
    today = pd.Timestamp.today().normalize()
    current_month = today.month
    current_year = today.year

    inventory_count = int(inventory_df["Producto"].dropna().nunique()) if not inventory_df.empty else 0
    total_stock = float(inventory_df["Stock actual"].sum()) if not inventory_df.empty else 0
    low_stock = (
        int(inventory_df["Estado"].eq("Bajo stock").sum())
        if not inventory_df.empty
        else 0
    )
    no_stock = (
        int((inventory_df["Estado"].eq("Sin stock") | inventory_df["Stock actual"].le(0)).sum())
        if not inventory_df.empty
        else 0
    )

    valid_dispatches = dispatch_df.dropna(subset=["Fecha"]) if not dispatch_df.empty else dispatch_df
    todays_dispatches = (
        int(valid_dispatches["Fecha"].dt.normalize().eq(today).sum())
        if not valid_dispatches.empty
        else 0
    )
    monthly_quantity = (
        float(
            valid_dispatches.loc[
                (valid_dispatches["Fecha"].dt.month == current_month)
                & (valid_dispatches["Fecha"].dt.year == current_year),
                "Cantidad",
            ].sum()
        )
        if not valid_dispatches.empty
        else 0
    )

    cols = st.columns(6)
    cols[0].metric("Productos PT", f"{inventory_count:,}")
    cols[1].metric("Stock total PT", f"{total_stock:,.0f}")
    cols[2].metric("Bajo stock", f"{low_stock:,}")
    cols[3].metric("Sin stock", f"{no_stock:,}")
    cols[4].metric("Despachos hoy", f"{todays_dispatches:,}")
    cols[5].metric("Despachado mes", f"{monthly_quantity:,.0f}")


def render_summary_tab(inventory_df: pd.DataFrame, dispatch_df: pd.DataFrame) -> None:
    st.subheader("Resumen general")
    render_kpis(inventory_df, dispatch_df)

    left, right = st.columns(2)
    with left:
        if inventory_df.empty:
            _plot_empty("No hay datos de inventario para graficar.")
        else:
            stock_by_product = (
                inventory_df.groupby("Producto", dropna=False, as_index=False)["Stock actual"]
                .sum()
                .sort_values("Stock actual", ascending=False)
            )
            fig = px.bar(
                stock_by_product,
                x="Producto",
                y="Stock actual",
                title="Stock actual por producto",
                color_discrete_sequence=["#1f77b4"],
            )
            fig.update_layout(xaxis_title="", yaxis_title="Stock actual")
            st.plotly_chart(fig, use_container_width=True)

    with right:
        if inventory_df.empty:
            _plot_empty("No hay estados de inventario disponibles.")
        else:
            state_counts = inventory_df["Estado"].fillna("Sin estado").value_counts().reset_index()
            state_counts.columns = ["Estado", "Cantidad"]
            fig = px.pie(
                state_counts,
                names="Estado",
                values="Cantidad",
                title="Distribución por estado",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        if dispatch_df.empty or dispatch_df["Fecha"].isna().all():
            _plot_empty("No hay despachos fechados para graficar.")
        else:
            dispatches_by_day = (
                dispatch_df.dropna(subset=["Fecha"])
                .assign(Dia=lambda data: data["Fecha"].dt.date)
                .groupby("Dia", as_index=False)["Numero despacho"]
                .count()
                .rename(columns={"Numero despacho": "Despachos"})
            )
            fig = px.bar(
                dispatches_by_day,
                x="Dia",
                y="Despachos",
                title="Despachos por día",
                color_discrete_sequence=["#4c78a8"],
            )
            fig.update_layout(xaxis_title="", yaxis_title="Despachos")
            st.plotly_chart(fig, use_container_width=True)

    with right:
        if dispatch_df.empty:
            _plot_empty("No hay productos despachados para graficar.")
        else:
            top_products = (
                dispatch_df.groupby("Producto", dropna=False, as_index=False)["Cantidad"]
                .sum()
                .sort_values("Cantidad", ascending=False)
                .head(10)
            )
            fig = px.bar(
                top_products,
                x="Cantidad",
                y="Producto",
                title="Top 10 productos más despachados",
                orientation="h",
                color_discrete_sequence=["#59a14f"],
            )
            fig.update_layout(xaxis_title="Cantidad", yaxis_title="")
            st.plotly_chart(fig, use_container_width=True)


def render_inventory_tab(inventory_df: pd.DataFrame) -> None:
    st.subheader("Inventario PT")

    if inventory_df.empty:
        st.info("La hoja Inventario_PT no tiene datos para mostrar.")
        return

    filter_cols = st.columns(3)
    selected_products = filter_cols[0].multiselect(
        "Producto",
        options=_select_options(inventory_df, "Producto"),
        key="inventory_product_filter",
    )
    selected_presentations = filter_cols[1].multiselect(
        "Presentación",
        options=_select_options(inventory_df, "Presentacion"),
        key="inventory_presentation_filter",
    )
    selected_states = filter_cols[2].multiselect(
        "Estado",
        options=_select_options(inventory_df, "Estado"),
        key="inventory_state_filter",
    )

    filtered = _filter_by_multiselect(inventory_df, "Producto", selected_products)
    filtered = _filter_by_multiselect(filtered, "Presentacion", selected_presentations)
    filtered = _filter_by_multiselect(filtered, "Estado", selected_states)

    st.dataframe(filtered, use_container_width=True, hide_index=True)

    if filtered.empty:
        st.warning("No hay inventario con los filtros seleccionados.")
        return

    chart_df = filtered.sort_values("Stock actual", ascending=False)
    fig = px.bar(
        chart_df,
        x="Producto",
        y=["Stock actual", "Stock minimo"],
        barmode="group",
        title="Stock actual vs stock mínimo",
        color_discrete_sequence=["#1f77b4", "#d62728"],
    )
    fig.update_layout(xaxis_title="", yaxis_title="Unidades")
    st.plotly_chart(fig, use_container_width=True)

    critical = filtered[
        filtered["Estado"].isin(CRITICAL_STATES)
        | filtered["Stock actual"].le(0)
        | filtered["Stock actual"].lt(filtered["Stock minimo"])
    ]
    st.subheader("Productos críticos")
    if critical.empty:
        st.success("No hay productos críticos con los filtros actuales.")
    else:
        st.dataframe(critical, use_container_width=True, hide_index=True)


def render_dispatch_tab(dispatch_df: pd.DataFrame) -> None:
    st.subheader("Despachos")

    if dispatch_df.empty:
        st.info("La hoja Despachos no tiene datos para mostrar.")
        return

    valid_dates = dispatch_df["Fecha"].dropna()
    if valid_dates.empty:
        min_date = max_date = pd.Timestamp.today().date()
    else:
        min_date = valid_dates.min().date()
        max_date = valid_dates.max().date()

    filter_cols = st.columns(4)
    selected_range = filter_cols[0].date_input(
        "Rango de fechas",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        key="dispatch_date_filter",
    )
    selected_clients = filter_cols[1].multiselect(
        "Cliente",
        options=_select_options(dispatch_df, "Cliente"),
        key="dispatch_client_filter",
    )
    selected_products = filter_cols[2].multiselect(
        "Producto",
        options=_select_options(dispatch_df, "Producto"),
        key="dispatch_product_filter",
    )
    selected_states = filter_cols[3].multiselect(
        "Estado",
        options=_select_options(dispatch_df, "Estado"),
        key="dispatch_state_filter",
    )

    filtered = dispatch_df.copy()

    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        start_date, end_date = selected_range
        filtered = filtered[
            filtered["Fecha"].isna()
            | (
                (filtered["Fecha"].dt.date >= start_date)
                & (filtered["Fecha"].dt.date <= end_date)
            )
        ]

    filtered = _filter_by_multiselect(filtered, "Cliente", selected_clients)
    filtered = _filter_by_multiselect(filtered, "Producto", selected_products)
    filtered = _filter_by_multiselect(filtered, "Estado", selected_states)

    st.dataframe(filtered, use_container_width=True, hide_index=True)

    if filtered.empty:
        st.warning("No hay despachos con los filtros seleccionados.")
        return

    left, right = st.columns(2)
    with left:
        dated = filtered.dropna(subset=["Fecha"])
        if dated.empty:
            _plot_empty("No hay fechas válidas para el gráfico de despachos por día.")
        else:
            dispatches_by_day = (
                dated.assign(Dia=lambda data: data["Fecha"].dt.date)
                .groupby("Dia", as_index=False)["Numero despacho"]
                .count()
                .rename(columns={"Numero despacho": "Despachos"})
            )
            fig = px.line(
                dispatches_by_day,
                x="Dia",
                y="Despachos",
                markers=True,
                title="Despachos por día",
                color_discrete_sequence=["#1f77b4"],
            )
            fig.update_layout(xaxis_title="", yaxis_title="Despachos")
            st.plotly_chart(fig, use_container_width=True)

    with right:
        quantity_by_product = (
            filtered.groupby("Producto", dropna=False, as_index=False)["Cantidad"]
            .sum()
            .sort_values("Cantidad", ascending=False)
        )
        fig = px.bar(
            quantity_by_product,
            x="Producto",
            y="Cantidad",
            title="Cantidad despachada por producto",
            color_discrete_sequence=["#59a14f"],
        )
        fig.update_layout(xaxis_title="", yaxis_title="Cantidad")
        st.plotly_chart(fig, use_container_width=True)

    client_summary = (
        filtered.groupby("Cliente", dropna=False, as_index=False)["Numero despacho"]
        .count()
        .rename(columns={"Numero despacho": "Despachos"})
        .sort_values("Despachos", ascending=False)
    )
    fig = px.bar(
        client_summary,
        x="Cliente",
        y="Despachos",
        title="Despachos por cliente",
        color_discrete_sequence=["#f28e2b"],
    )
    fig.update_layout(xaxis_title="", yaxis_title="Despachos")
    st.plotly_chart(fig, use_container_width=True)


def render_alerts_tab(inventory_df: pd.DataFrame, dispatch_df: pd.DataFrame) -> None:
    st.subheader("Alertas")

    if inventory_df.empty:
        st.info("No hay inventario para evaluar alertas.")
        return

    no_stock = inventory_df[
        inventory_df["Estado"].eq("Sin stock") | inventory_df["Stock actual"].le(0)
    ]
    low_stock = inventory_df[inventory_df["Estado"].eq("Bajo stock")]
    below_minimum = inventory_df[inventory_df["Stock actual"].lt(inventory_df["Stock minimo"])]

    if no_stock.empty and low_stock.empty and below_minimum.empty:
        st.success("Inventario PT sin alertas críticas en este momento.")
    else:
        if not no_stock.empty:
            st.error(f"{len(no_stock)} producto(s) sin stock.")
            st.dataframe(no_stock, use_container_width=True, hide_index=True)

        if not low_stock.empty:
            st.warning(f"{len(low_stock)} producto(s) en bajo stock.")
            st.dataframe(low_stock, use_container_width=True, hide_index=True)

        if not below_minimum.empty:
            st.warning(f"{len(below_minimum)} producto(s) por debajo del stock mínimo.")
            st.dataframe(below_minimum, use_container_width=True, hide_index=True)

    st.subheader("Productos con mayor salida")
    if dispatch_df.empty:
        st.info("No hay despachos para calcular mayor salida.")
        return

    top_movement = (
        dispatch_df.groupby("Producto", dropna=False, as_index=False)["Cantidad"]
        .sum()
        .sort_values("Cantidad", ascending=False)
        .head(10)
    )

    if top_movement.empty or top_movement["Cantidad"].sum() <= 0:
        st.info("No hay cantidades despachadas válidas para calcular mayor salida.")
        return

    st.dataframe(top_movement, use_container_width=True, hide_index=True)
    fig = px.bar(
        top_movement,
        x="Cantidad",
        y="Producto",
        orientation="h",
        title="Mayor salida según despachos",
        color_discrete_sequence=["#4c78a8"],
    )
    fig.update_layout(xaxis_title="Cantidad", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    apply_style()

    if not check_password():
        return

    if st_autorefresh is not None:
        st_autorefresh(interval=60_000, key="dashboard_pt_refresh")
    else:
        st.info("Instala streamlit-autorefresh para activar la actualización cada 60 segundos.")

    st.title("Dashboard PT - GEA")
    st.caption("Consulta gerencial solo lectura de inventario PT y despachos.")

    inventory_raw, dispatch_raw, last_query = load_data()

    inventory_missing = validate_columns(inventory_raw, INVENTORY_COLUMNS, INVENTORY_SHEET)
    dispatch_missing = validate_columns(dispatch_raw, DISPATCH_COLUMNS, DISPATCH_SHEET)

    if inventory_missing or dispatch_missing:
        st.stop()

    inventory_df = clean_inventory_data(inventory_raw)
    dispatch_df = clean_dispatch_data(dispatch_raw)

    st.markdown(
        f'<p class="gea-muted">Última consulta: {last_query:%d/%m/%Y %H:%M:%S}</p>',
        unsafe_allow_html=True,
    )

    if inventory_df.empty and dispatch_df.empty:
        st.info("Google Sheets no tiene datos disponibles para mostrar.")
        return

    summary_tab, inventory_tab, dispatch_tab, alerts_tab = st.tabs(
        ["Resumen general", "Inventario PT", "Despachos", "Alertas"]
    )

    with summary_tab:
        render_summary_tab(inventory_df, dispatch_df)

    with inventory_tab:
        render_inventory_tab(inventory_df)

    with dispatch_tab:
        render_dispatch_tab(dispatch_df)

    with alerts_tab:
        render_alerts_tab(inventory_df, dispatch_df)


if __name__ == "__main__":
    main()
