"""ncWMS client for HCDC THREDDS SCHISM-WWM interpolated wave forecasts."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests

WMS_NS = {"wms": "http://www.opengis.net/wms"}
WMS_VERSION = "1.3.0"

THREDDS_WMS_BASE = (
    "https://thredds.hcdc.hereon.de/thredds/wms/"
    "PO-Flow/gb_schism_interpol_wave"
)
DATASET_TEMPLATE = "schism-wwm_interp_{ymd}.nc"

# German Bight fallback extent (from GetCapabilities)
DEFAULT_BBOX = (5.11358851, 53.03607668, 10.40358851, 55.63107668)


@dataclass(frozen=True)
class WmsLayer:
    name: str
    title: str
    bbox: tuple[float, float, float, float]  # west, south, east, north
    time_dimension: str | None
    styles: list[str]


def dataset_url(forecast_date: date) -> str:
    ymd = forecast_date.strftime("%Y%m%d")
    return f"{THREDDS_WMS_BASE}/{DATASET_TEMPLATE.format(ymd=ymd)}"


def _get_text(element: ET.Element | None, tag: str) -> str | None:
    if element is None:
        return None
    child = element.find(f"wms:{tag}", WMS_NS)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _parse_bbox(layer_el: ET.Element) -> tuple[float, float, float, float]:
    geo = layer_el.find("wms:EX_GeographicBoundingBox", WMS_NS)
    if geo is not None:
        west = float(_get_text(geo, "westBoundLongitude"))
        east = float(_get_text(geo, "eastBoundLongitude"))
        south = float(_get_text(geo, "southBoundLatitude"))
        north = float(_get_text(geo, "northBoundLatitude"))
        return west, south, east, north
    return DEFAULT_BBOX


def _parse_styles(layer_el: ET.Element) -> list[str]:
    styles: list[str] = []
    for style_el in layer_el.findall("wms:Style", WMS_NS):
        name = _get_text(style_el, "Name")
        if name:
            styles.append(name)
    return styles


def parse_capabilities(xml_text: str) -> list[WmsLayer]:
    root = ET.fromstring(xml_text)
    layers: list[WmsLayer] = []

    for layer_el in root.findall(".//wms:Layer[@queryable='1']", WMS_NS):
        name = _get_text(layer_el, "Name")
        if not name:
            continue

        title = _get_text(layer_el, "Title") or name
        dim_el = layer_el.find("wms:Dimension[@name='time']", WMS_NS)
        time_dim = dim_el.text.strip() if dim_el is not None and dim_el.text else None
        styles = _parse_styles(layer_el)
        if not styles:
            continue

        layers.append(
            WmsLayer(
                name=name,
                title=title,
                bbox=_parse_bbox(layer_el),
                time_dimension=time_dim,
                styles=styles,
            )
        )

    return layers


def default_style(layer: WmsLayer) -> str:
    for preferred in ("default-scalar/default", "default-vector/default", "default-arrows"):
        if preferred in layer.styles:
            return preferred
    return layer.styles[0]


def fetch_capabilities(forecast_date: date, timeout: int = 60) -> tuple[str, list[WmsLayer]]:
    url = dataset_url(forecast_date)
    params = {"service": "WMS", "version": WMS_VERSION, "request": "GetCapabilities"}
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    layers = parse_capabilities(response.text)
    if not layers:
        raise ValueError("No queryable WMS layers found in GetCapabilities response.")
    return url, layers


def normalize_timestep(value: str, day: date) -> str:
    """Convert ncWMS timestep to full ISO8601 for WMS TIME (GetMap).

    GetMetadata ``timesteps`` returns time-of-day only, e.g. ``01:00:00.000+00:00``,
    for the requested ``day``. Capabilities use full instants, e.g.
    ``2026-05-11T01:00:00.000Z``.
    """
    value = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    time_part = value
    if "+" in value:
        time_part = value.split("+", 1)[0]
    elif value.endswith("Z"):
        time_part = value[:-1]

    return f"{day.isoformat()}T{time_part}Z"


def fetch_timesteps(
    wms_url: str,
    layer_name: str,
    day: date,
    timeout: int = 60,
) -> list[str]:
    """Return ISO8601 timesteps for a calendar day (ncWMS GetMetadata)."""
    params = {
        "request": "GetMetadata",
        "item": "timesteps",
        "layerName": layer_name,
        "day": day.isoformat(),
    }
    response = requests.get(wms_url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        raw = [str(t) for t in data]
    elif isinstance(data, dict) and "timesteps" in data:
        raw = [str(t) for t in data["timesteps"]]
    else:
        raise ValueError(f"Unexpected timesteps response: {data!r}")
    return [normalize_timestep(t, day) for t in raw]


_ISO_INTERVAL = re.compile(
    r"^(?P<start>.+?)/(?P<end>.+?)/(?P<step>PT(?P<hours>\d+)H)$"
)


def timesteps_from_dimension(dimension: str) -> list[str]:
    """Parse WMS time dimension like start/end/PT1H into ISO8601 instants."""
    match = _ISO_INTERVAL.match(dimension.strip())
    if not match:
        return []

    start = datetime.fromisoformat(match.group("start").replace("Z", "+00:00"))
    end = datetime.fromisoformat(match.group("end").replace("Z", "+00:00"))
    hours = int(match.group("hours"))
    step = timedelta(hours=hours)

    times: list[str] = []
    current = start
    while current <= end:
        times.append(current.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        current += step
    return times


def fetch_layer_details(
    wms_url: str,
    layer_name: str,
    time_iso: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """ncWMS GetMetadata ``layerDetails`` (palettes, scale range, units, …)."""
    params: dict[str, str] = {
        "request": "GetMetadata",
        "item": "layerDetails",
        "layerName": layer_name,
    }
    if time_iso is not None:
        params["TIME"] = time_iso
    response = requests.get(wms_url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected layerDetails response: {data!r}")
    return data


def available_timesteps(
    wms_url: str,
    layer: WmsLayer,
    forecast_date: date,
) -> list[str]:
    """Timesteps for browsing; tries GetMetadata then capabilities dimension."""
    try:
        return fetch_timesteps(wms_url, layer.name, forecast_date)
    except (requests.RequestException, ValueError):
        pass

    if layer.time_dimension:
        return timesteps_from_dimension(layer.time_dimension)

    return []


def build_getmap_params(
    layer_name: str,
    style: str,
    time_iso: str,
    *,
    palette: str | None = None,
    colorscale_range: tuple[float, float] | None = None,
    opacity: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "service": "WMS",
        "version": WMS_VERSION,
        "request": "GetMap",
        "layers": layer_name,
        "styles": style,
        "format": "image/png",
        "transparent": "true",
        "time": time_iso,
    }
    if palette is not None:
        params["palette"] = palette
    if colorscale_range is not None:
        params["colorscalerange"] = f"{colorscale_range[0]},{colorscale_range[1]}"
    if opacity is not None:
        params["opacity"] = str(opacity)
    return params


def legend_url(
    wms_url: str,
    layer_name: str = "",
    style: str = "",
    *,
    palette: str = "default",
    time_iso: str | None = None,
    colorscale_range: tuple[float, float] | None = None,
    height: int = 650,
    width: int = 110,
    colorbar_only: bool = False,
    vertical: bool = True,
) -> str:
    """Build GetLegendGraphic URL sized to match the map height."""
    vert = "true" if vertical else "false"
    if colorbar_only:
        params: dict[str, Any] = {
            "REQUEST": "GetLegendGraphic",
            "COLORBARONLY": "true",
            "PALETTE": palette,
            "VERTICAL": vert,
            "HEIGHT": str(height),
            "WIDTH": str(width),
            "NUMCOLORBANDS": "250",
        }
    else:
        params = {
            "REQUEST": "GetLegendGraphic",
            "LAYERS": layer_name,
            "STYLES": style,
            "COLORBARONLY": "false",
            "VERTICAL": vert,
            "HEIGHT": str(height),
            "WIDTH": str(width),
        }
    if time_iso is not None:
        params["TIME"] = time_iso
    if colorscale_range is not None:
        params["COLORSCALERANGE"] = f"{colorscale_range[0]},{colorscale_range[1]}"
    return f"{wms_url}?{urlencode(params)}"


@dataclass(frozen=True)
class TimeSeries:
    latitude: float
    longitude: float
    times: list[datetime]
    values: list[float]
    value_label: str


def lat_lon_to_query_pixel(
    lat: float,
    lon: float,
    bbox: tuple[float, float, float, float],
    *,
    width: int = 101,
    height: int = 101,
) -> tuple[int, int, int, int]:
    """Map WGS84 click to WMS 1.3.0 ``I``/``J`` indices for a layer bounding box."""
    west, south, east, north = bbox
    lon_span = east - west
    lat_span = north - south
    if lon_span <= 0 or lat_span <= 0:
        raise ValueError(f"Invalid bbox: {bbox}")

    i = int(round((lon - west) / lon_span * (width - 1)))
    j = int(round((north - lat) / lat_span * (height - 1)))
    i = max(0, min(width - 1, i))
    j = max(0, min(height - 1, j))
    return i, j, width, height


def time_range_param(timesteps: list[str]) -> str:
    if not timesteps:
        raise ValueError("No timesteps for time series request.")
    if len(timesteps) == 1:
        return timesteps[0]
    return f"{timesteps[0]}/{timesteps[-1]}"


def parse_timeseries_csv(csv_text: str) -> TimeSeries:
    latitude: float | None = None
    longitude: float | None = None
    value_label = "value"
    times: list[datetime] = []
    values: list[float] = []
    in_data = False

    for raw_line in csv_text.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# Latitude:"):
            latitude = float(line.split(":", 1)[1].strip())
            continue
        if line.startswith("# Longitude:"):
            longitude = float(line.split(":", 1)[1].strip())
            continue
        if line.startswith("Time ") or line.startswith("Time,"):
            if "," in line:
                value_label = line.split(",", 1)[1].strip()
            in_data = True
            continue
        if in_data:
            time_str, value_str = line.split(",", 1)
            times.append(datetime.fromisoformat(time_str.replace("Z", "+00:00")))
            values.append(float(value_str))

    if latitude is None or longitude is None:
        raise ValueError("Timeseries CSV missing latitude/longitude header.")
    if not times:
        raise ValueError("Timeseries CSV contains no data rows.")

    return TimeSeries(
        latitude=latitude,
        longitude=longitude,
        times=times,
        values=values,
        value_label=value_label,
    )


def fetch_timeseries(
    wms_url: str,
    layer_name: str,
    style: str,
    bbox: tuple[float, float, float, float],
    lat: float,
    lon: float,
    timesteps: list[str],
    timeout: int = 60,
) -> TimeSeries:
    """Point time series via ncWMS ``GetTimeseries`` (CSV)."""
    west, south, east, north = bbox
    i, j, width, height = lat_lon_to_query_pixel(lat, lon, bbox)
    params = {
        "SERVICE": "WMS",
        "VERSION": WMS_VERSION,
        "REQUEST": "GetTimeseries",
        "LAYERS": layer_name,
        "QUERY_LAYERS": layer_name,
        "STYLES": style,
        "CRS": "EPSG:4326",
        "BBOX": f"{south},{west},{north},{east}",
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "I": str(i),
        "J": str(j),
        "TIME": time_range_param(timesteps),
        "INFO_FORMAT": "text/csv",
    }
    response = requests.get(wms_url, params=params, timeout=timeout)
    response.raise_for_status()
    return parse_timeseries_csv(response.text)


def format_time_label(iso_time: str, day: date | None = None) -> str:
    iso_time = normalize_timestep(iso_time, day) if day is not None else iso_time
    if not re.match(r"^\d{4}-\d{2}-\d{2}", iso_time.strip()):
        raise ValueError(f"Cannot format timestep without a day: {iso_time!r}")
    dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M UTC")
