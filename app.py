"""Streamlit viewer for HCDC ncWMS SCHISM-WWM wave forecasts."""

from __future__ import annotations

import base64
from datetime import date

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

from colormaps import palette_options, style_with_palette
from wms_client import (
    WmsLayer,
    available_timesteps,
    build_getmap_params,
    dataset_url,
    default_style,
    fetch_capabilities,
    fetch_layer_details,
    fetch_timeseries,
    format_time_label,
    legend_url,
    normalize_timestep,
)

HCDC_CATALOG_URL = "https://thredds.hcdc.hereon.de/thredds/catalog.html"
KSD_URL = "https://www.hereon.de/institutes/coastal_systems_analysis_modeling/hydrodynamics_data_assimilation/index.php.en"
HCDC_WAVE_CATALOG_URL = (
    "https://thredds.hcdc.hereon.de/thredds/catalog/PO-Flow/gb_schism_interpol_wave/catalog.html"
)
HCDC_PO_FLOW_CATALOG_URL = "https://thredds.hcdc.hereon.de/thredds/catalog/PO-Flow/catalog.html"

st.set_page_config(
    page_title="ForeCastView – German Bight WMS",
    page_icon="🌊",
    layout="wide",
)

DEFAULT_LAYER = "Hs"
MAP_HEIGHT = 650


@st.cache_data(show_spinner="Loading WMS capabilities…")
def load_capabilities(forecast_date: date):
    return fetch_capabilities(forecast_date)


@st.cache_data(show_spinner="Loading timesteps…")
def load_timesteps(wms_url: str, layer_name: str, layer_time_dim: str | None, forecast_date: date):
    from wms_client import WmsLayer, available_timesteps

    layer = WmsLayer(
        name=layer_name,
        title=layer_name,
        bbox=(0, 0, 0, 0),
        time_dimension=layer_time_dim,
        styles=[],
    )
    return available_timesteps(wms_url, layer, forecast_date)


@st.cache_data(show_spinner="Loading palettes…")
def load_layer_palettes(wms_url: str, layer_name: str, time_iso: str | None) -> tuple[list[str], str]:
    details = fetch_layer_details(wms_url, layer_name, time_iso)
    palettes = details.get("palettes") or ["default"]
    default_palette = details.get("defaultPalette") or "default"
    return palettes, default_palette


@st.cache_data(show_spinner=False)
def load_palette_preview(wms_url: str, palette: str) -> bytes:
    preview_url = legend_url(
        wms_url,
        palette=palette,
        colorbar_only=True,
        height=28,
        width=280,
        vertical=False,
    )
    return load_legend_image(preview_url)


@st.cache_data(show_spinner="Loading time series…")
def load_timeseries(
    wms_url: str,
    layer_name: str,
    style: str,
    bbox: tuple[float, float, float, float],
    lat: float,
    lon: float,
    timesteps_key: tuple[str, ...],
):
    return fetch_timeseries(
        wms_url,
        layer_name,
        style,
        bbox,
        lat,
        lon,
        list(timesteps_key),
    )


@st.cache_data(show_spinner=False)
def load_legend_image(url: str) -> bytes:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def render_legend(png_bytes: bytes, height: int) -> None:
    """Render WMS legend aligned to map height; white panel avoids dark-theme text clash."""
    b64 = base64.b64encode(png_bytes).decode()
    st.markdown(
        f'<div class="forecast-legend-panel" style="height:{height}px;background:#ffffff;'
        f"border:1px solid #ddd;border-radius:4px;display:flex;align-items:center;"
        f'justify-content:center;overflow:hidden;box-sizing:border-box;">'
        f'<img alt="Legend" src="data:image/png;base64,{b64}" '
        f'style="max-height:100%;height:100%;width:auto;object-fit:contain;display:block;" />'
        f"</div>",
        unsafe_allow_html=True,
    )


def make_map(
    wms_url: str,
    layer_name: str,
    style: str,
    time_iso: str,
    bbox: tuple[float, float, float, float],
    opacity: int,
    colorscale: tuple[float, float] | None,
    marker: tuple[float, float] | None = None,
) -> folium.Map:
    west, south, east, north = bbox
    center = [(south + north) / 2, (west + east) / 2]

    fmap = folium.Map(location=center, zoom_start=8, tiles="OpenStreetMap")

    wms_extra: dict[str, str] = {"TIME": time_iso}
    if colorscale is not None:
        wms_extra["COLORSCALERANGE"] = f"{colorscale[0]},{colorscale[1]}"
    if opacity is not None:
        wms_extra["OPACITY"] = str(opacity)

    folium.WmsTileLayer(
        url=wms_url,
        layers=layer_name,
        styles=style,
        fmt="image/png",
        transparent=True,
        version="1.3.0",
        attr="HCDC THREDDS / GCOAST SCHISM-WWM",
        overlay=True,
        control=True,
        name=layer_name,
        **wms_extra,
    ).add_to(fmap)

    if marker is not None:
        folium.Marker(
            location=marker,
            tooltip=f"{marker[0]:.3f}°N, {marker[1]:.3f}°E",
            icon=folium.Icon(color="red", icon="info-sign"),
        ).add_to(fmap)

    folium.LayerControl().add_to(fmap)
    fmap.fit_bounds([[south, west], [north, east]])
    return fmap


def render_about_header(layers: list[WmsLayer]) -> None:
    """Descriptive text and collapsible list of variables from the active WMS dataset."""
    variable_list_html = "\n".join(
        f"<li>{layer.title} (<code>{layer.name}</code>)</li>"
        for layer in sorted(layers, key=lambda layer: layer.title.lower())
    )
    n_layers = len(layers)

    st.markdown(
        """
<style>
details.forecast-variable-list { margin: 0.35em 0 0.85em 0; }
details.forecast-variable-list summary { cursor: pointer; font-weight: 500; }
details.forecast-variable-list ul { margin: 0.5em 0 0 1.1em; padding: 0; }
</style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
The Hereon pre-operational hydrodynamic forecast system for the German Bight is
operated by the department [Hydrodynamics and Data Assimilation]({KSD_URL}). It provides
daily updated hydrodynamic forecasts for the German Bight area, covering
a nowcast and a two-day forecast.

Key parameters provided by the system include:

<details class="forecast-variable-list">
<summary>Show available variables ({n_layers})</summary>
<ul>
{variable_list_html}
</ul>
</details>

These outputs, in an interpolated form, are made available via the services of the
[Helmholtz Coastal Data Center (HCDC)]({HCDC_CATALOG_URL}):
[HCDC THREDDS catalog — interpolated waves]({HCDC_WAVE_CATALOG_URL}).

*Fig. 2 — Related GCOAST SCHISM routine product:*
[GCOAST_schism_routine_Sea_surface_velocities_BJ]({HCDC_PO_FLOW_CATALOG_URL})
(sea surface velocities, [PO-Flow catalog]({HCDC_PO_FLOW_CATALOG_URL})).

The forecast system is based on the **SCHISM** (Semi-implicit Cross-scale Hydroscience
Integrated System Model; Zhang et al. 2016) modeling system applied on an unstructured
grid representation of the German Bight (Stanev et al. 2019). The grid consists of
approximately 500,000 nodes and 1 million triangular elements in the horizontal
dimension, along with 21 terrain-following sigma layers in the vertical dimension.
The horizontal resolution ranges from ~1.5 km at the open boundary to ~50 m in river
cross-sectional areas.
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.title("Hereon pre-operational hydrodynamic forecast system for the German Bight")

    with st.sidebar:
        st.header("Forecast selection")

        forecast_date = st.date_input(
            "Forecast day (dataset date in URL)",
            value=date.today(),
            help="Selects `schism-wwm_interp_YYYYMMDD.nc` on the THREDDS server.",
        )

    try:
        wms_url, layers = load_capabilities(forecast_date)
    except requests.RequestException as exc:
        st.error(f"Could not load dataset for {forecast_date:%Y-%m-%d}: {exc}")
        st.info(f"Expected URL: `{dataset_url(forecast_date)}`")
        st.stop()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    layer_by_name = {layer.name: layer for layer in layers}
    layer_names = list(layer_by_name.keys())

    render_about_header(layers)
    st.divider()

    with st.sidebar:
        default_index = layer_names.index(DEFAULT_LAYER) if DEFAULT_LAYER in layer_names else 0
        selected_name = st.selectbox(
            "Variable",
            options=layer_names,
            index=default_index,
            format_func=lambda n: f"{n} – {layer_by_name[n].title}",
        )
        selected_layer = layer_by_name[selected_name]

        style = st.selectbox("Style", options=selected_layer.styles, index=selected_layer.styles.index(default_style(selected_layer)) if default_style(selected_layer) in selected_layer.styles else 0)

        st.divider()
        st.subheader("Time")

        timesteps = load_timesteps(
            wms_url,
            selected_name,
            selected_layer.time_dimension,
            forecast_date,
        )

        if not timesteps:
            st.warning("No timesteps for this forecast/layer.")
            st.stop()

        normalized = [normalize_timestep(t, forecast_date) for t in timesteps]
        time_labels = {format_time_label(t, forecast_date): iso for t, iso in zip(timesteps, normalized)}
        label_list = list(time_labels.keys())

        selected_label = st.select_slider(
            "Hour (browse forecast times)",
            options=label_list,
            value=label_list[min(len(label_list) // 2, len(label_list) - 1)],
        )
        time_iso = time_labels[selected_label]

        st.divider()
        st.subheader("Display")

        opacity = st.slider("Layer opacity (%)", min_value=10, max_value=100, value=85)

        try:
            server_palettes, default_palette = load_layer_palettes(wms_url, selected_name, time_iso)
        except requests.RequestException as exc:
            st.error(f"Could not load palettes: {exc}")
            st.stop()

        cmap_options = palette_options(server_palettes)
        if default_palette not in cmap_options:
            cmap_options = [default_palette, *cmap_options]
        cmap_index = cmap_options.index(default_palette) if default_palette in cmap_options else 0

        show_inverted = st.checkbox("Show inverted palettes", value=False)
        if show_inverted:
            cmap_options = palette_options(server_palettes, include_inverted=True)
            if default_palette not in cmap_options:
                cmap_options = [default_palette, *cmap_options]
            cmap_index = cmap_options.index(default_palette) if default_palette in cmap_options else 0

        selected_palette = st.selectbox("Colormap", options=cmap_options, index=cmap_index)
        try:
            st.image(
                load_palette_preview(wms_url, selected_palette),
                caption="Preview",
                use_container_width=True,
            )
        except requests.RequestException:
            pass

        plot_style = style_with_palette(style, selected_palette)

        use_custom_scale = st.checkbox("Custom colour scale", value=True)
        colorscale: tuple[float, float] | None = None
        if use_custom_scale:
            cmin, cmax = st.number_input("Min", value=0.0), st.number_input("Max", value=3.0)
            colorscale = (cmin, cmax)

    # Keep colour-bar column flush with the Folium iframe (same height, top-aligned).
    st.markdown(
        f"""
        <style>
        [data-testid="stHorizontalBlock"]:has(iframe[title="streamlit_folium.st_folium"])
        [data-testid="column"]:last-child .stMarkdown {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        [data-testid="stHorizontalBlock"]:has(iframe[title="streamlit_folium.st_folium"])
        [data-testid="column"]:last-child .forecast-legend-panel {{
            height: {MAP_HEIGHT}px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    col_map, col_legend = st.columns([4, 1], vertical_alignment="top")

    if "ts_marker" not in st.session_state:
        st.session_state.ts_marker = None

    ts_context = (str(forecast_date), selected_name, wms_url)
    if st.session_state.get("ts_context") != ts_context:
        st.session_state.ts_context = ts_context
        st.session_state.ts_marker = None

    with col_map:
        marker = st.session_state.ts_marker
        fmap = make_map(
            wms_url=wms_url,
            layer_name=selected_name,
            style=plot_style,
            time_iso=time_iso,
            bbox=selected_layer.bbox,
            opacity=opacity,
            colorscale=colorscale,
            marker=marker,
        )
        map_state = st_folium(
            fmap,
            width=None,
            height=MAP_HEIGHT,
            returned_objects=["last_clicked"],
            key="forecast_map",
        )
        st.caption(
            "Fig. 1 — German Bight model domain (interactive map). "
            "Click to extract a time series."
        )

        click = map_state.get("last_clicked") if map_state else None
        if click and click.get("lat") is not None and click.get("lng") is not None:
            new_marker = (float(click["lat"]), float(click["lng"]))
            if new_marker != st.session_state.ts_marker:
                st.session_state.ts_marker = new_marker
                st.rerun()

    with col_legend:
        leg_url = legend_url(
            wms_url,
            selected_name,
            plot_style,
            time_iso=time_iso,
            colorscale_range=colorscale,
            height=MAP_HEIGHT,
            colorbar_only=False,
        )
        try:
            render_legend(load_legend_image(leg_url), MAP_HEIGHT)
        except requests.RequestException:
            st.caption("Colour bar could not be loaded.")

    if st.session_state.ts_marker is not None:
        ts_lat, ts_lon = st.session_state.ts_marker
        st.subheader("Time series")
        st.caption(
            f"Point: **{ts_lat:.4f}°N**, **{ts_lon:.4f}°E** · "
            f"{layer_by_name[selected_name].title}"
        )
        try:
            series = load_timeseries(
                wms_url,
                selected_name,
                plot_style,
                selected_layer.bbox,
                ts_lat,
                ts_lon,
                tuple(normalized),
            )
            ts_df = pd.DataFrame(
                {series.value_label: series.values},
                index=pd.to_datetime(series.times, utc=True),
            )
            st.line_chart(ts_df, height=280)
        except (requests.RequestException, ValueError) as exc:
            st.error(f"Could not load time series: {exc}")

    with st.expander("Example WMS GetMap URL"):
        params = build_getmap_params(
            selected_name,
            plot_style,
            time_iso,
            colorscale_range=colorscale,
            opacity=opacity,
        )
        query = "&".join(f"{k}={v}" for k, v in params.items())
        st.code(f"{wms_url}?{query}&crs=EPSG:3857&bbox=…&width=512&height=512", language=None)


if __name__ == "__main__":
    main()
