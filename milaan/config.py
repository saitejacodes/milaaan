"""Typed TOML configuration and integer fee arithmetic."""

from __future__ import annotations

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
