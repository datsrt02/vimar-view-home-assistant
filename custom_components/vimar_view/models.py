"""Data models for Vimar VIEW."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class VimarPlant:
    """A Vimar plant."""

    id: str
    name: str
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class VimarDevice:
    """A Vimar device or gateway."""

    id: str
    name: str
    plant_id: str | None = None
    model: str | None = None
    manufacturer: str = "Vimar"
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class VimarDataEntity:
    """An extracted data point from a Vimar document."""

    id: str
    name: str
    platform: str
    value: Any
    plant_id: str | None = None
    device_id: str | None = None
    category: str | None = None
    path: str | None = None
    enabled: bool | None = None
    features: JsonObject = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class VimarRoutine:
    """A routine or scene exposed by the Vimar cloud API."""

    id: str
    name: str
    plant_id: str
    raw: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class VimarSystemData:
    """A full Vimar account snapshot."""

    plants: dict[str, VimarPlant] = field(default_factory=dict)
    devices: dict[str, VimarDevice] = field(default_factory=dict)
    entities: dict[str, VimarDataEntity] = field(default_factory=dict)
    routines: dict[str, VimarRoutine] = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)
