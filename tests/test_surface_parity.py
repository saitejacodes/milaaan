"""The report and the dashboard must show the same numbers.

Both surfaces are driven here from one real run: the HTML report is rendered,
and the Streamlit app is executed against a recording stand-in for ``streamlit``
that captures every value it would display. Each required figure is then
required to appear in both.

The stand-in also serves as a crash test for the dashboard: every code path the
app takes on load runs here, which an import check alone would not exercise.
"""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory
from milaan.report.render import render_report
from milaan.view import GateNotPassed, build_view


class Recorder:
    """Minimal streamlit stand-in that records everything it is shown."""

    def __init__(self) -> None:
        self.values: list[str] = []
        self.stopped = False

    # -- capture ---------------------------------------------------------

    def _record(self, *args: object, **kwargs: object) -> None:
        for value in list(args) + list(kwargs.values()):
            if isinstance(value, (str, int, float)):
                self.values.append(str(value))
            elif isinstance(value, dict):
                self.values.append(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    self._record(item)

    def metric(self, label: object = "", value: object = "", delta: object = "",
               **_kwargs: object) -> None:
        self._record(label, value, delta)

    def dataframe(self, data: object = None, **_kwargs: object) -> None:
        self._record(data)

    def __getattr__(self, name: str):
        # title, caption, markdown, subheader, write, info, warning, success,
        # error, json, code -- all of them display something worth recording.
        def display(*args: object, **kwargs: object) -> None:
            self._record(*args, **kwargs)
        return display

    # -- layout ----------------------------------------------------------

    def set_page_config(self, **_kwargs: object) -> None:
        return None

    def columns(self, spec: object) -> list["Recorder"]:
        count = spec if isinstance(spec, int) else len(spec)  # type: ignore[arg-type]
        return [self for _ in range(count)]

    def tabs(self, labels: list[str]) -> list[contextlib.AbstractContextManager]:
        self._record(labels)
        return [contextlib.nullcontext() for _ in labels]

    def expander(self, label: object = "", **_kwargs: object):
        self._record(label)
        return contextlib.nullcontext()

    # -- widgets ---------------------------------------------------------

    def radio(self, _label: object, options: list[str], **_kwargs: object) -> str:
        return options[0]

    def selectbox(self, _label: object, options: list[str], **_kwargs: object) -> str:
        return options[0] if options else ""

    def text_input(self, _label: object, value: str = "", **_kwargs: object) -> str:
        return value

    def button(self, *_args: object, **_kwargs: object) -> bool:
        return False

    def stop(self) -> None:
        self.stopped = True


def _text(recorder: Recorder) -> str:
    return "\n".join(recorder.values)


class SurfaceParityTests(unittest.TestCase):
    html: str
    dashboard_text: str
    view_values: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = tempfile.TemporaryDirectory(prefix="milaan-parity-")
        run = Path(cls._root.name) / "run"
        generate_to_directory(600, 77, "mixed", run)
        run_pipeline(run, run / "m.db", "mock")
        evaluate_run(run, run / "m.db", run, "mixed")
        render_report(run, run / "m.db", run / "report.html")
        cls.html = (run / "report.html").read_text(encoding="utf-8")

        recorder = Recorder()
        import milaan.dashboard as dashboard

        with patch.dict("sys.modules", {"streamlit": recorder}), \
                patch.object(dashboard, "_arguments",
                             return_value=types.SimpleNamespace(run_dir=run, db=run / "m.db")):
            dashboard.main()
        cls.dashboard_text = _text(recorder)
        cls.recorder = recorder

        view = build_view(run, run / "m.db")
        metrics = view.metrics
        cash = {row["label"]: row for row in view.cash}
        coverage = {row["label"]: row for row in view.coverage}
        cls.view = view
        cls.view_values = {
            "plane A precision": f"{metrics['planes']['A']['match_precision']['rate']:.2%}",
            "plane A recall": f"{metrics['planes']['A']['expected_match_recall']['rate']:.2%}",
            "plane B precision": f"{metrics['planes']['B']['match_precision']['rate']:.2%}",
            "plane B recall": f"{metrics['planes']['B']['expected_match_recall']['rate']:.2%}",
            "exception precision": f"{metrics['exceptions']['precision']['rate']:.2%}",
            "exception recall": f"{metrics['exceptions']['recall']['rate']:.2%}",
            "orders coverage": coverage["Eligible orders auto-matched"]["percent"],
            "payments coverage": coverage["Gateway payments auto-matched"]["percent"],
            "batches coverage": coverage["Settlement batches banked"]["percent"],
            "bank line coverage": coverage["Bank lines explained by a match"]["percent"],
            "banked cash": cash["Verified banked"]["amount"],
            "expected unbanked": cash["Expected but unbanked"]["amount"],
            "blocked exposure": cash["Blocked settlements"]["amount"],
            "unexplained credits": cash["Unexplained bank credits"]["amount"],
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls._root.cleanup()

    def test_dashboard_renders_without_error(self) -> None:
        self.assertFalse(self.recorder.stopped, "the dashboard bailed out on a valid run")
        self.assertTrue(self.dashboard_text)

    def test_every_headline_figure_agrees_across_both_surfaces(self) -> None:
        for name, value in self.view_values.items():
            with self.subTest(figure=name):
                self.assertIn(value, self.html, f"{name} missing from the report")
                self.assertIn(value, self.dashboard_text, f"{name} missing from the dashboard")

    def test_counts_agree_across_both_surfaces(self) -> None:
        metrics = self.view.metrics
        for name, value in (
            ("false matches", str(metrics["false_match_count"])),
            ("exception count", str(len(self.view.exceptions))),
            ("physical source records",
             f"{metrics['source_record_conservation']['total_source_records']:,}"),
        ):
            with self.subTest(figure=name):
                self.assertIn(value, self.html)
                self.assertIn(value, self.dashboard_text)

    def test_conservation_and_amount_delta_agree(self) -> None:
        metrics = self.view.metrics
        conservation = f"{metrics['source_record_conservation']['rate']:.2%}"
        delta = str(metrics["amount_conservation"]["delta_paise"])
        self.assertIn(conservation, self.html)
        self.assertIn(conservation, self.dashboard_text)
        self.assertIn(f"{delta} paise delta", self.html)
        self.assertIn(f"{delta} paise delta", self.dashboard_text)

    def test_throughput_agrees(self) -> None:
        rate = f"{self.view.throughput['records_per_second']:,.0f}"
        self.assertIn(rate, self.html)
        self.assertIn(rate, self.dashboard_text)

    def test_both_surfaces_carry_the_mandatory_coverage_disclaimer(self) -> None:
        from milaan.view import COVERAGE_DISCLAIMER

        self.assertIn(COVERAGE_DISCLAIMER, self.html)
        self.assertIn(COVERAGE_DISCLAIMER, self.dashboard_text)

    def test_both_surfaces_translate_internal_plane_names(self) -> None:
        for label in ("Order → Payment reconciliation", "Settlement → Bank reconciliation"):
            self.assertIn(label, self.html)
            self.assertIn(label, self.dashboard_text)

    def test_every_exception_is_listed_on_both_surfaces(self) -> None:
        self.assertTrue(self.view.exceptions)
        for item in self.view.exceptions:
            with self.subTest(exception=item.exception_id):
                self.assertIn(item.exception_id, self.html)
                self.assertIn(item.exception_id, self.dashboard_text)
                self.assertIn(item.exposure, self.html)
                self.assertIn(item.exposure, self.dashboard_text)

    def test_neither_surface_recomputes_a_financial_figure(self) -> None:
        """Structural guard on the two ways a surface could invent a figure.

        Converting paise to rupees, and summing paise, are the only arithmetic a
        presentation layer would plausibly reach for. Both belong in the view
        model; finding either here means the surfaces can drift.
        """
        root = Path(__file__).parents[1]
        for name in ("milaan/report/render.py", "milaan/dashboard.py"):
            source = (root / name).read_text(encoding="utf-8")
            for pattern, description in (
                (r"/\s*100\b", "converts paise to rupees itself"),
                (r"\bsum\s*\(", "aggregates values itself"),
                (r"_paise\s*[-+*]\s*\w", "does arithmetic on a paise field"),
            ):
                with self.subTest(file=name, rule=description):
                    self.assertEqual(re.findall(pattern, source), [],
                                     f"{name} {description}")

    def test_a_failed_gate_is_refused_by_both_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "run"
            broken.mkdir()
            metrics = dict(self.view.metrics)
            metrics["gate"] = {"name": "mixed", "status": "FAIL", "failures": ["forced"]}
            (broken / "functional_metrics.json").write_text(json.dumps(metrics))
            with self.assertRaises(GateNotPassed):
                build_view(broken, Path(tmp) / "missing.db")


if __name__ == "__main__":
    unittest.main()
