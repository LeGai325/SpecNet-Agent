from __future__ import annotations

import unittest
from pathlib import Path

from specnet_agent.analysis import common
from specnet_agent.analysis.plot_action_distribution import ACTIONS, draw_action_mix_svg

TEST_OUTPUT = Path(__file__).resolve().parents[1] / "fixtures"


class AnalysisSmokeTest(unittest.TestCase):
    def test_standard_library_svg_path(self) -> None:
        row = {
            "policy": "specnet_agent_qw_1_60",
            "group": "specnet",
            "quality_weight": 1.6,
            "total_actions": 10,
            **{f"{action}_pct": 0.2 for action in ACTIONS},
        }
        output = TEST_OUTPUT / "_actions_smoke.svg"
        try:
            draw_action_mix_svg([row], str(output), "smoke", "smoke", include_rules=False)
            self.assertIn("<svg", output.read_text(encoding="utf-8"))
        finally:
            output.unlink(missing_ok=True)

    def test_matplotlib_png_and_pdf_path(self) -> None:
        common.setup_style()
        try:
            fig, ax = common.plt.subplots()
            ax.plot([0, 1], [0, 1])
            common.save_figure(fig, str(TEST_OUTPUT), "_figure_smoke", dpi=72)
            common.plt.close(fig)
            self.assertTrue((TEST_OUTPUT / "_figure_smoke.png").exists())
            self.assertTrue((TEST_OUTPUT / "_figure_smoke.pdf").exists())
        finally:
            common.plt.close("all")
            (TEST_OUTPUT / "_figure_smoke.png").unlink(missing_ok=True)
            (TEST_OUTPUT / "_figure_smoke.pdf").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
