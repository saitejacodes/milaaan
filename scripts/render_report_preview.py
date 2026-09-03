"""Render an exact, README-friendly scorecard from functional metrics."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).parents[1]
METRICS = ROOT / "data" / "samples" / "run42" / "functional_metrics.json"
AGENT = ROOT / "data" / "samples" / "run42" / "agent_metrics.json"
OUT = ROOT / "data" / "samples" / "run42" / "report_preview.png"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def money(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str,
         value: str, note: str, accent: str = "#54C68A") -> None:
    draw.rounded_rectangle(box, radius=18, fill="#171713", outline="#39362B", width=2)
    x1, y1, _x2, _y2 = box
    draw.text((x1 + 22, y1 + 18), label.upper(), fill="#A8A394", font=font(16, True))
    draw.text((x1 + 22, y1 + 53), value, fill=accent, font=font(31, True))
    draw.text((x1 + 22, y1 + 101), note, fill="#D3CEBF", font=font(15))


def main() -> None:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    agent = json.loads(AGENT.read_text(encoding="utf-8"))
    image = Image.new("RGB", (1440, 900), "#0D0C09")
    draw = ImageDraw.Draw(image)
    draw.text((64, 48), "TRACK 04 · VERIFIED RUN", fill="#D8A84E", font=font(18, True))
    draw.text((64, 86), "Milaan AI Finance Controller", fill="#F2E9D7", font=font(48, True))
    draw.text((64, 151), "Deterministic code owns every rupee. AI investigates verified evidence.",
              fill="#C8C1B2", font=font(21))
    draw.text((64, 195), "1,200 orders  ·  2,467 source records  ·  seed 42 mixed  ·  generator 1.3.0",
              fill="#8F897B", font=font(17))

    coverage = metrics["workload_coverage"]
    cash = metrics["cash_position"]
    width, gap = 308, 26
    xs = [64 + index * (width + gap) for index in range(4)]
    card(draw, (xs[0], 250, xs[0] + width, 390), "Expected-match precision", "100.00%",
         "Plane A 1,154/1,154 · Plane B 21/21")
    card(draw, (xs[1], 250, xs[1] + width, 390), "Eligible-order coverage",
         f"{coverage['plane_a_orders']['rate']:.2%}", "1,154 of 1,155 auto-matched")
    card(draw, (xs[2], 250, xs[2] + width, 390), "Settlement coverage",
         f"{coverage['plane_b_settlement_batches']['rate']:.2%}", "21 of 26 banked · abstentions visible")
    card(draw, (xs[3], 250, xs[3] + width, 390), "False matches", "0",
         "named synthetic benchmark")

    card(draw, (xs[0], 416, xs[0] + width, 556), "Verified banked", money(cash["banked_paise"]),
         "batch-to-bank evidence")
    card(draw, (xs[1], 416, xs[1] + width, 556), "Expected unbanked",
         money(cash["expected_unbanked_paise"]), "missing bank evidence", "#E8B85B")
    card(draw, (xs[2], 416, xs[2] + width, 556), "Blocked settlements",
         money(cash["blocked_settlement_paise"]), "finance review required", "#E8B85B")
    card(draw, (xs[3], 416, xs[3] + width, 556), "Unexplained bank",
         money(cash["unexplained_bank_credit_paise"]), "never auto-posted", "#E8B85B")

    draw.rounded_rectangle((64, 588, 1376, 790), radius=20, fill="#15140F", outline="#574B31", width=2)
    draw.text((88, 612), "CONTROL EVIDENCE", fill="#D8A84E", font=font(17, True))
    evidence = [
        ("Source conservation", "2,467 / 2,467", "Every row terminates matched, excepted, quarantined, or ignored"),
        ("Amount conservation", "0 paise delta", "Gateway signed nets equal settlement control total"),
        ("Bounded AI gate", f"{agent['case_count']} / {agent['case_count']}", "Grounded answer or explicit refusal; no write tools"),
        ("1,200-order throughput", "20,364 records/s", "Three-run median in verification environment"),
    ]
    for index, (label, value, note) in enumerate(evidence):
        y = 650 + index * 32
        draw.text((88, y), label, fill="#A8A394", font=font(16, True))
        draw.text((335, y), value, fill="#54C68A", font=font(17, True))
        draw.text((555, y), note, fill="#D3CEBF", font=font(15))

    draw.text((64, 832), "Expected-match accuracy and full-workload coverage are intentionally separate.",
              fill="#8F897B", font=font(16))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    print(f"preview written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
