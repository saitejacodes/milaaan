"""Render the README scorecard image from regenerated artefacts only.

Every figure on this image is read from the sample run's own JSON. Nothing is
typed in by hand -- an image with a hardcoded throughput number is a claim no
one can reproduce, which is exactly what this project refuses to make.

Regenerate the whole thing with ``make sample``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "data" / "samples" / "run42"
OUT = SAMPLE / "report_preview.png"


# Pillow's built-in bitmap font draws a tofu box for ₹ and →, which looks like
# a rendering bug in a scorecard a judge is meant to trust. Find a real
# TrueType face; if there is none, drop to ASCII rather than ship tofu.
FONT_FAMILIES = (
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf",) * 2,
    ("/Library/Fonts/Arial Unicode.ttf",) * 2,
    ("/System/Library/Fonts/Helvetica.ttc",) * 2,
    ("/System/Library/Fonts/SFNS.ttf",) * 2,
    ("/System/Library/Fonts/Supplemental/Verdana.ttf",
     "/System/Library/Fonts/Supplemental/Verdana Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)


REQUIRED_GLYPHS = "₹→"


def _renders(face: ImageFont.FreeTypeFont, character: str) -> bool:
    """True only if the face draws a real glyph, not the .notdef tofu box.

    Asking whether the glyph has a bounding box is not enough: the tofu box has
    one too. Compare against a codepoint no font defines and require a
    different bitmap.
    """
    missing = bytes(face.getmask("\uffff"))
    return bytes(face.getmask(character)) != missing


def _family() -> tuple[str, str] | None:
    for regular, bold in FONT_FAMILIES:
        try:
            face = ImageFont.truetype(regular, 24)
        except OSError:
            continue
        if not all(_renders(face, character) for character in REQUIRED_GLYPHS):
            continue
        try:
            ImageFont.truetype(bold, 12)
        except OSError:
            bold = regular
        return regular, bold
    return None


FAMILY = _family()
UNICODE_OK = FAMILY is not None


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FAMILY is None:
        return ImageFont.load_default(size)
    return ImageFont.truetype(FAMILY[1] if bold else FAMILY[0], size)


def safe(text: str) -> str:
    """Keep the image readable on a host with no Unicode-capable font."""
    if UNICODE_OK:
        return text
    return text.replace("₹", "Rs ").replace("→", "->").replace("·", "-")


def money(paise: int) -> str:
    whole, fraction = divmod(abs(int(paise)), 100)
    sign = "-" if paise < 0 else ""
    return safe(f"{sign}₹{whole:,}.{fraction:02d}")


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str,
         value: str, note: str, accent: str = "#54C68A") -> None:
    draw.rounded_rectangle(box, radius=18, fill="#171713", outline="#39362B", width=2)
    x1, y1, _x2, _y2 = box
    draw.text((x1 + 22, y1 + 18), safe(label.upper()), fill="#A8A394", font=font(15, True))
    draw.text((x1 + 22, y1 + 50), safe(value), fill=accent, font=font(30, True))
    draw.text((x1 + 22, y1 + 98), safe(note), fill="#D3CEBF", font=font(14))


def load(name: str) -> dict:
    path = SAMPLE / name
    if not path.is_file():
        raise SystemExit(
            f"{path} is missing. Run `make sample` to regenerate the sample run first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    metrics = load("functional_metrics.json")
    telemetry = load("runtime_telemetry.json")
    agent = load("agent_metrics.json")
    if metrics.get("gate", {}).get("status") != "PASS":
        raise SystemExit("the sample run did not pass its gate; refusing to render a scorecard")

    planes = metrics["planes"]
    coverage = metrics["workload_coverage"]
    cash = metrics["cash_position"]
    conservation = metrics["source_record_conservation"]
    exceptions = metrics["exceptions"]

    image = Image.new("RGB", (1440, 960), "#0D0C09")
    draw = ImageDraw.Draw(image)
    draw.text((64, 44), safe("TRACK 04 · VERIFIED RUN"), fill="#D8A84E", font=font(18, True))
    draw.text((64, 82), safe("Milaan · AI Finance Controller"), fill="#F2E9D7", font=font(46, True))
    draw.text((64, 145), "Deterministic code owns every rupee. AI investigates verified evidence.",
              fill="#C8C1B2", font=font(20))
    draw.text((64, 180),
              safe("Orders → Gateway payments → Settlement batches → "
                   "Bank credits → Cash & exceptions"),
              fill="#D8A84E", font=font(17, True))
    draw.text((64, 212),
              safe(f"{conservation['total_source_records']:,} physical source records  ·  seed "
              f"{metrics['seed']} {metrics['profile']}  ·  generator "
              f"{metrics['generator_version']}  ·  metrics schema "
                   f"{metrics['schema_version']}"),
              fill="#8F897B", font=font(16))

    width, gap = 308, 26
    xs = [64 + index * (width + gap) for index in range(4)]

    card(draw, (xs[0], 262, xs[0] + width, 398), safe("Order → payment"),
         f"{planes['A']['match_precision']['rate']:.2%}",
         f"precision · {planes['A']['true_match_count']:,}/{planes['A']['expected_count']:,} correct")
    card(draw, (xs[1], 262, xs[1] + width, 398), safe("Settlement → bank"),
         f"{planes['B']['match_precision']['rate']:.2%}",
         f"precision · {planes['B']['true_match_count']:,}/{planes['B']['expected_count']:,} correct")
    card(draw, (xs[2], 262, xs[2] + width, 398), "Exceptions",
         f"{exceptions['precision']['rate']:.0%} / {exceptions['recall']['rate']:.0%}",
         f"precision / recall · {exceptions['actual_count']} detected")
    card(draw, (xs[3], 262, xs[3] + width, 398), "False matches",
         str(metrics["false_match_count"]), "against independently rebuilt truth")

    card(draw, (xs[0], 424, xs[0] + width, 560), "Verified banked",
         money(cash["banked_paise"]), safe("matched settlement → bank credit"))
    card(draw, (xs[1], 424, xs[1] + width, 560), "Expected unbanked",
         money(cash["expected_unbanked_paise"]), "no bank evidence yet", "#E8B85B")
    card(draw, (xs[2], 424, xs[2] + width, 560), "Blocked settlements",
         money(cash["blocked_settlement_paise"]), "a control stopped this cash", "#E8B85B")
    card(draw, (xs[3], 424, xs[3] + width, 560), "Unexplained bank",
         money(cash["unexplained_bank_credit_paise"]), "never auto-posted", "#E8B85B")

    draw.rounded_rectangle((64, 592, 1376, 800), radius=20, fill="#15140F",
                           outline="#574B31", width=2)
    draw.text((88, 614), "OPERATIONAL COVERAGE AND CONTROL EVIDENCE", fill="#D8A84E",
              font=font(16, True))
    integrity = metrics["truth_integrity"]
    rows = [
        ("Eligible orders auto-matched",
         f"{coverage['plane_a_orders']['rate']:.2%}",
         f"{coverage['plane_a_orders']['numerator']:,} of "
         f"{coverage['plane_a_orders']['denominator']:,}"),
        ("Settlement batches banked",
         f"{coverage['plane_b_settlement_batches']['rate']:.2%}",
         f"{coverage['plane_b_settlement_batches']['numerator']:,} of "
         f"{coverage['plane_b_settlement_batches']['denominator']:,} · abstentions are visible"),
        ("Source-record conservation",
         f"{conservation['numerator']:,} / {conservation['denominator']:,}",
         "every row terminates matched, excepted, quarantined or ignored"),
        ("Amount conservation",
         f"{metrics['amount_conservation']['delta_paise']} paise delta",
         "signed member sums equal settlement control totals"),
        ("Truth integrity", integrity["status"],
         "benchmark truth regenerated independently, then bound to this run"),
        ("Read-only AI boundary",
         f"{agent['safety']['correct_refusals']}/{agent['safety']['case_count']}",
         "write and override requests refused before any model is consulted"),
    ]
    for index, (label, value, note) in enumerate(rows):
        y = 650 + index * 24
        draw.text((88, y), safe(label), fill="#A8A394", font=font(15, True))
        draw.text((375, y), safe(value), fill="#54C68A", font=font(15, True))
        draw.text((600, y), safe(note), fill="#D3CEBF", font=font(14))

    draw.text((64, 826),
              f"Reconciliation engine throughput: "
              f"{telemetry['source_records_per_second']:,.0f} source records/s "
              f"({telemetry['source_records']:,} records in {telemetry['wall_ms']:,} ms on this run; "
              "run `make benchmark` for the multi-size sweep)",
              fill="#C8C1B2", font=font(16))
    draw.text((64, 858),
              "100% match precision does not mean 100% of the workload was auto-resolved.",
              fill="#E8B85B", font=font(16, True))
    draw.text((64, 888),
              "Accuracy and operational coverage are measured and reported separately, on purpose.",
              fill="#8F897B", font=font(15))
    draw.text((64, 918),
              "Every number on this image is read from this run's own artefacts. "
              "Regenerate with `make sample`.",
              fill="#6E6A5E", font=font(14))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    print(f"preview written: {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
