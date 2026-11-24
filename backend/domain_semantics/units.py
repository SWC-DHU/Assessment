"""Shared unit canonicalization and dimensional conversion utilities."""
from __future__ import annotations

from .catalog import load_vocabulary

# (dimension, linear_scale_to_base, offset_to_base)
UNIT_SPECS: dict[str, tuple[str, float, float]] = {
    # Time -> s
    "ms": ("time", 1e-3, 0.0),
    "s": ("time", 1.0, 0.0),
    "min": ("time", 60.0, 0.0),
    "h": ("time", 3600.0, 0.0),
    # Length -> mm
    "um": ("length", 1e-3, 0.0),
    "mm": ("length", 1.0, 0.0),
    "m": ("length", 1000.0, 0.0),
    # Pressure -> Pa
    "Pa": ("pressure", 1.0, 0.0),
    "kPa": ("pressure", 1e3, 0.0),
    "bar": ("pressure", 1e5, 0.0),
    "MPa": ("pressure", 1e6, 0.0),
    # Temperature -> K
    "K": ("temperature", 1.0, 0.0),
    "°C": ("temperature", 1.0, 273.15),
    # Mass -> kg
    "g": ("mass", 1e-3, 0.0),
    "kg": ("mass", 1.0, 0.0),
    "t": ("mass", 1000.0, 0.0),
    # Power -> W
    "W": ("power", 1.0, 0.0),
    "kW": ("power", 1000.0, 0.0),
    # Force -> N
    "N": ("force", 1.0, 0.0),
    "kN": ("force", 1000.0, 0.0),
    # Voltage -> V
    "mV": ("voltage", 1e-3, 0.0),
    "V": ("voltage", 1.0, 0.0),
    "kV": ("voltage", 1000.0, 0.0),
    # Resistance -> Ohm
    "mOhm": ("resistance", 1e-3, 0.0),
    "Ohm": ("resistance", 1.0, 0.0),
    "MOhm": ("resistance", 1e6, 0.0),
    # Speed -> mm/s
    "mm/s": ("speed", 1.0, 0.0),
    "m/min": ("speed", 1000.0 / 60.0, 0.0),
    "m/s": ("speed", 1000.0, 0.0),
    # Ratio / Count
    "%": ("ratio", 1.0, 0.0),
    "件": ("count", 1.0, 0.0),
    "个": ("count", 1.0, 0.0),
    "模组": ("count", 1.0, 0.0),
}


def canonical_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    cleaned = unit.strip()
    if not cleaned:
        return None
    aliases = load_vocabulary().get("unit_aliases", {})
    return aliases.get(cleaned, cleaned)


def unit_dimension(unit: str | None) -> str | None:
    canon = canonical_unit(unit)
    if canon is None:
        return None
    spec = UNIT_SPECS.get(canon)
    return spec[0] if spec else None


def to_base_unit(value: float, unit: str | None) -> tuple[float, str] | None:
    """Convert value into its canonical dimension base value and dimension name."""
    canon = canonical_unit(unit)
    if canon is None or canon not in UNIT_SPECS:
        return None
    dim, scale, offset = UNIT_SPECS[canon]
    return value * scale + offset, dim


def convert_unit(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert numeric value between compatible units. Returns None if incompatible."""
    src = canonical_unit(from_unit)
    dst = canonical_unit(to_unit)
    if src is None or dst is None:
        return None
    if src == dst:
        return float(value)
    src_spec = UNIT_SPECS.get(src)
    dst_spec = UNIT_SPECS.get(dst)
    if not src_spec or not dst_spec or src_spec[0] != dst_spec[0]:
        return None
    base_val = value * src_spec[1] + src_spec[2]
    return (base_val - dst_spec[2]) / dst_spec[1]
