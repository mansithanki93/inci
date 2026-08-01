from __future__ import annotations

from io import StringIO
import unittest
from unittest import mock

from inci_tennis_expert.contracts import MatchStatus
from inci_tennis_runtime.shadow_cli import (
    main,
    sample_monitor_views,
    terminal_frame,
)
from inci_tennis_runtime.shadow_runtime import SyncDisplayState


class ShadowCliTests(unittest.TestCase):
    def test_terminal_frame_uses_ansi_only_when_allowed(self):
        text = "monitor"
        self.assertTrue(terminal_frame(text, is_tty=True).startswith("\x1b["))
        self.assertEqual(terminal_frame(text, is_tty=False), "monitor\n")
        self.assertEqual(
            terminal_frame(text, is_tty=True, no_ansi=True),
            "monitor\n",
        )

    def test_sample_is_deterministic_and_reaches_trusted_state(self):
        first = sample_monitor_views()
        second = sample_monitor_views()

        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 3)
        self.assertTrue(
            all(
                row.sync_state is SyncDisplayState.TRUSTED
                for row in first[-1].contracts
            )
        )
        self.assertEqual(first[-1].match_status, MatchStatus.LIVE)

    def test_main_prints_final_sample_without_ansi_on_non_tty(self):
        output = StringIO()
        self.assertEqual(main(("--sample",), output=output, is_tty=False), 0)
        rendered = output.getvalue()
        self.assertIn("SYNTHETIC DISPLAY SAMPLE", rendered)
        self.assertIn("trusted_synchronized", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_main_rejects_unknown_or_unsafe_arguments(self):
        for arguments in (
            (),
            ("--unknown",),
            ("--sample", "--width", "20"),
            ("--sample", "--width", "not-a-number"),
        ):
            with self.subTest(arguments=arguments):
                output = StringIO()
                self.assertEqual(
                    main(arguments, output=output, is_tty=False),
                    2,
                )
                self.assertIn("usage:", output.getvalue())

    def test_all_stages_is_plain_and_separated(self):
        output = StringIO()
        self.assertEqual(
            main(
                ("--sample", "--all-stages", "--width", "100"),
                output=output,
                is_tty=True,
            ),
            0,
        )
        value = output.getvalue()
        self.assertNotIn("\x1b[", value)
        self.assertGreaterEqual(value.count("INCI TENNIS SHADOW"), 3)

    def test_renderer_failure_falls_back_to_plain_diagnostic(self):
        output = StringIO()
        with mock.patch(
            "inci_tennis_runtime.shadow_cli.render_monitor",
            side_effect=ValueError("sensitive detail"),
        ):
            self.assertEqual(
                main(("--sample",), output=output, is_tty=True),
                1,
            )
        value = output.getvalue()
        self.assertIn("INCI TENNIS SHADOW | RENDER ERROR | NO ORDERS", value)
        self.assertIn("reason=ValueError", value)
        self.assertNotIn("sensitive detail", value)
        self.assertNotIn("\x1b[", value)


if __name__ == "__main__":
    unittest.main()
