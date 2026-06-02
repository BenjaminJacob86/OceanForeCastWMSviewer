"""Streamlit viewer for HCDC ncWMS SCHISM-WWM wave forecasts."""

from __future__ import annotations

import base64
import time
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
FIG_CAPTION = (
    "Fig. 1 — German Bight model domain (interactive map). "
    "Click to extract a time series."
)


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
def load_layer_palettes(wms_url: str, layer_name: str) -> tuple[list[str], str]:
    """Palette list for a layer (independent of timestep)."""
    details = fetch_layer_details(wms_url, layer_name, time_iso=None)
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
    #st.title("Hereon pre-operational hydrodynamic forecast system for the German Bight")
    st.title("German Bight hdrodynamic forecast")
    with st.sidebar:
        st.image("https://www.hereon.de/cms60/res/assets/logos/hereon_logo.svg")
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

    # render_about_header(layers)
    # st.divider()

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
        default_idx = min(len(label_list) // 2, len(label_list) - 1)

        time_slider_ctx = (str(forecast_date), selected_name, tuple(label_list))
        if st.session_state.get("time_slider_ctx") != time_slider_ctx:
            st.session_state.time_slider_ctx = time_slider_ctx
            st.session_state.ts_index = default_idx
            st.session_state.hour_slider = label_list[default_idx]
            st.session_state.playing = False

        if "ts_index" not in st.session_state:
            st.session_state.ts_index = default_idx
        if "playing" not in st.session_state:
            st.session_state.playing = False

        st.session_state.ts_index = max(0, min(st.session_state.ts_index, len(label_list) - 1))

        play_col, speed_col = st.columns([1, 2])
        with play_col:
            play_label = "Pause" if st.session_state.playing else "Play"
            play_icon = "⏸" if st.session_state.playing else "▶"
            if st.button(f"{play_icon} {play_label}", use_container_width=True):
                was_playing = st.session_state.playing
                st.session_state.playing = not st.session_state.playing
                if st.session_state.playing and not was_playing:
                    st.session_state.play_colormap = st.session_state.get(
                        "colormap_picker", "default"
                    )
                    st.session_state.play_use_custom_scale = st.session_state.get(
                        "use_custom_scale", True
                    )
                    if st.session_state.play_use_custom_scale:
                        st.session_state.play_colorscale = (
                            float(st.session_state.get("cscale_min", 0.0)),
                            float(st.session_state.get("cscale_max", 3.0)),
                        )
                    else:
                        st.session_state.play_colorscale = None
                    st.session_state.play_plot_style = style_with_palette(
                        style, st.session_state.play_colormap
                    )
        with speed_col:
            play_speed = st.selectbox(
                "Step interval",
                options=[0.5, 1.0, 2.0],
                format_func=lambda s: f"{s:g} s / step",
                index=1,
                label_visibility="collapsed",
            )

        st.session_state.label_list = label_list
        st.session_state.time_labels = time_labels
        st.session_state.normalized_times = tuple(normalized)
        st.session_state.play_interval = float(play_speed)

        # Important: Streamlit forbids mutating a widget-key after it is instantiated.
        # During autoplay we therefore show a separate, disabled slider with a different key.
        if st.session_state.playing:
            st.select_slider(
                "Hour (browse forecast times)",
                options=label_list,
                value=label_list[st.session_state.ts_index],
                key="hour_slider_play",
                disabled=True,
            )
        else:
            if st.session_state.get("hour_slider") not in label_list:
                st.session_state.hour_slider = label_list[st.session_state.ts_index]
            st.select_slider(
                "Hour (browse forecast times)",
                options=label_list,
                key="hour_slider",
            )
            st.session_state.ts_index = label_list.index(st.session_state.hour_slider)

        st.divider()
        st.subheader("Display")

        opacity = st.slider("Layer opacity (%)", min_value=10, max_value=100, value=85)

        show_inverted = st.checkbox("Show inverted palettes", value=False)

        palette_ctx = (selected_name, wms_url, show_inverted)
        if st.session_state.get("palette_ctx") != palette_ctx:
            try:
                server_palettes, default_palette = load_layer_palettes(wms_url, selected_name)
            except requests.RequestException as exc:
                st.error(f"Could not load palettes: {exc}")
                st.stop()
            st.session_state.palette_ctx = palette_ctx
            st.session_state.server_palettes = server_palettes
            st.session_state.palette_default = default_palette
            st.session_state.colormap_picker = default_palette
            st.session_state.play_colormap = default_palette
            st.session_state.play_plot_style = style_with_palette(style, default_palette)

        server_palettes = st.session_state.server_palettes
        default_palette = st.session_state.palette_default

        cmap_options = palette_options(server_palettes, include_inverted=show_inverted)
        if default_palette not in cmap_options:
            cmap_options = [default_palette, *cmap_options]

        if st.session_state.get("colormap_picker") not in cmap_options:
            st.session_state.colormap_picker = (
                default_palette if default_palette in cmap_options else cmap_options[0]
            )

        st.selectbox(
            "Colormap",
            options=cmap_options,
            key="colormap_picker",
        )

        if st.session_state.playing:
            selected_palette = st.session_state.get("play_colormap", st.session_state.colormap_picker)
        else:
            selected_palette = st.session_state.colormap_picker
            st.session_state.play_colormap = selected_palette

        try:
            st.image(
                load_palette_preview(wms_url, selected_palette),
                caption="Preview",
                use_container_width=True,
            )
        except requests.RequestException:
            pass

        if "cscale_min" not in st.session_state:
            st.session_state.cscale_min = 0.0
        if "cscale_max" not in st.session_state:
            st.session_state.cscale_max = 3.0

        use_custom_scale = st.checkbox("Custom colour scale", value=True, key="use_custom_scale")
        st.number_input("Min", key="cscale_min")
        st.number_input("Max", key="cscale_max")

        if st.session_state.playing:
            plot_style = st.session_state.play_plot_style
            colorscale = st.session_state.play_colorscale
        else:
            plot_style = style_with_palette(style, selected_palette)
            colorscale = None
            if use_custom_scale:
                colorscale = (
                    float(st.session_state.cscale_min),
                    float(st.session_state.cscale_max),
                )
            st.session_state.play_plot_style = plot_style
            st.session_state.play_colorscale = colorscale

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
        hr.forecast-map-separator {{
            border: none;
            border-top: 1px solid #8ecae6;
            margin: 1.25rem 0 0.65rem 0;
        }}
        p.forecast-fig-caption {{
            font-size: 1.125rem;
            line-height: 1.5;
            margin: 0 0 0.75rem 0;
            color: inherit;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<hr class="forecast-map-separator" />', unsafe_allow_html=True)
    st.markdown(
        f'<p class="forecast-fig-caption">{FIG_CAPTION}</p>',
        unsafe_allow_html=True,
    )

    col_map, col_legend = st.columns([4, 1], vertical_alignment="top")

    if "ts_marker" not in st.session_state:
        st.session_state.ts_marker = None

    ts_context = (str(forecast_date), selected_name, wms_url)
    if st.session_state.get("ts_context") != ts_context:
        st.session_state.ts_context = ts_context
        st.session_state.ts_marker = None
        st.session_state.playing = False

    # Resolve timestep here (not from slider widget state while animating).
    normalized_list = list(st.session_state.normalized_times)
    ts_index = st.session_state.ts_index
    if st.session_state.playing:
        time_iso = normalized_list[ts_index]
        plot_style = st.session_state.play_plot_style
        colorscale = st.session_state.play_colorscale
    else:
        time_iso = st.session_state.time_labels[st.session_state.hour_slider]
        plot_style = st.session_state.play_plot_style
        colorscale = st.session_state.play_colorscale

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
            key=f"forecast_map_{ts_index}_{time_iso}",
        )

        click = map_state.get("last_clicked") if map_state else None
        if click and click.get("lat") is not None and click.get("lng") is not None:
            new_marker = (float(click["lat"]), float(click["lng"]))
            if new_marker != st.session_state.ts_marker:
                st.session_state.ts_marker = new_marker
                st.rerun()

    with col_legend:
        leg_time = None if st.session_state.get("playing") else time_iso
        leg_url = legend_url(
            wms_url,
            selected_name,
            plot_style,
            time_iso=leg_time,
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
                st.session_state.normalized_times,
            )
            ts_df = pd.DataFrame(
                {series.value_label: series.values},
                index=pd.to_datetime(series.times, utc=True),
            )
            st.line_chart(ts_df, height=280)
        except (requests.RequestException, ValueError) as exc:
            st.error(f"Could not load time series: {exc}")

    # Advance animation after the map has been drawn for the current frame.
    if st.session_state.get("playing"):
        n_frames = len(st.session_state.normalized_times)
        st.session_state.ts_index = (st.session_state.ts_index + 1) % n_frames
        time.sleep(st.session_state.get("play_interval", 1.0))
        st.rerun()

if __name__ == "__main__":
    main()
