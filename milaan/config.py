"""Typed TOML configuration and integer fee arithmetic."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from milaan.models import Channel


@dataclass(frozen=True)
class ChannelFee:
    pct_bp: int
    flat_paise: int


@dataclass(frozen=True)
class FeeConfig:
    gst_rate_bp: int
    channels: dict[Channel, ChannelFee]


@dataclass(frozen=True)
class TimingConfig:
    settlement_cycle_bd: int
    bank_lag_bd_choices: tuple[int, ...]
    window_b_bd: int
    window_a_bd: int
    tol_b_paise: int


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_environment(path: Path | None = None) -> Path | None:
    """Load a small, predictable ``.env`` file without overriding the shell.

    The parser intentionally supports only ``KEY=value`` (optionally prefixed by
    ``export``) and single- or double-quoted values. Existing environment values
    always win. This keeps local BYO-LLM setup dependency-free and avoids the
    surprising interpolation and command execution semantics of a shell script.
    """
    configured = os.getenv("MILAAN_ENV_FILE")
    candidate = path or (Path(configured) if configured else repository_root() / ".env")
    if not candidate.is_file():
        return None
    for number, raw_line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ValueError(f"invalid environment entry at {candidate}:{number}")
        value = value.strip()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"unterminated quoted value at {candidate}:{number}")
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return candidate


def load_fees(path: Path | None = None) -> FeeConfig:
    raw = tomllib.loads((path or repository_root() / "config" / "fees.toml").read_text())
    channels = {
        Channel(name): ChannelFee(pct_bp=int(values["pct_bp"]),
                                  flat_paise=int(values["flat_paise"]))
        for name, values in raw["channels"].items()
    }
    return FeeConfig(gst_rate_bp=int(raw["gst_rate_bp"]), channels=channels)


def load_timing(path: Path | None = None) -> TimingConfig:
    raw = tomllib.loads((path or repository_root() / "config" / "timing.toml").read_text())
    return TimingConfig(
        settlement_cycle_bd=int(raw["settlement_cycle_bd"]),
        bank_lag_bd_choices=tuple(int(x) for x in raw["bank_lag_bd_choices"]),
        window_b_bd=int(raw["window_b_bd"]),
        window_a_bd=int(raw["window_a_bd"]),
        tol_b_paise=int(raw["tol_b_paise"]),
    )


def fee_for(gross_paise: int, channel: Channel, cfg: FeeConfig | None = None) -> tuple[int, int]:
    """Return fee and GST using half-up integer basis-point arithmetic."""
    if gross_paise < 0:
        raise ValueError("fee_for expects a non-negative gross amount")
    config = cfg or load_fees()
    rule = config.channels[channel]
    fee = rule.flat_paise + (gross_paise * rule.pct_bp + 5_000) // 10_000
    tax = (fee * config.gst_rate_bp + 5_000) // 10_000
    return fee, tax
