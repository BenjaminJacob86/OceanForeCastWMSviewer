"""Helpers for ncWMS palette / style names."""


def style_with_palette(style: str, palette: str) -> str:
    """Build ncWMS style ``stylename/palettename``."""
    if "/" in style:
        style_base, _old = style.split("/", 1)
        return f"{style_base}/{palette}"
    return f"{style}/{palette}"


def palette_options(palettes: list[str], *, include_inverted: bool = False) -> list[str]:
    """Sort palettes; optionally hide ``-inv`` variants."""
    if include_inverted:
        return sorted(palettes)
    return sorted(p for p in palettes if not p.endswith("-inv"))
