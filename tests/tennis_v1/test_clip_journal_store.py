from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.dont_write_bytecode = True

from inci_tennis_expert.contracts import DecisionAction, ExpertContractError
from inci_tennis_expert.clip_journal import scorecard_from_clip_records
from inci_tennis_io.clip_journal_store import (
    read_clip_journal_document,
    write_clip_journal_document,
)
from inci_tennis_runtime.scalp_paper_observer import PaperClipSession
from tests.tennis_v1.test_scalp_paper_observer import trusted_home_transition


class ClipJournalStoreTests(unittest.TestCase):
    def test_round_trip_persists_and_reloads_scorecard_inputs(self) -> None:
        transition, binding, prior = trusted_home_transition()
        session = PaperClipSession.with_default_bundle(
            require_calibration=False,
            session_id="clip-store-test",
        )
        observations = session.observe(transition, prior)
        records = session.journal_records()
        self.assertGreaterEqual(len(records), 1)
        self.assertIs(observations[0].action, DecisionAction.PAPER_BUY)

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "clip-session.json"
            integrity = write_clip_journal_document(path, records)
            self.assertEqual(len(integrity), 64)
            self.assertTrue(path.is_file())
            loaded = read_clip_journal_document(path)

        self.assertEqual(len(loaded), len(records))
        self.assertEqual(loaded[0].session_id, "clip-store-test")
        self.assertEqual(loaded[0].ticker, binding.home_market_ticker)
        self.assertEqual(loaded[0].record_sha256, records[0].record_sha256)
        self.assertEqual(
            loaded[0].lower_projected_net_pnl,
            records[0].lower_projected_net_pnl,
        )

        scorecard = scorecard_from_clip_records(
            records,
            session_id=session.session_id,
            target_net_pnl_usd=session.bundle.clip_artifact.target_net_pnl_usd,
        )
        self.assertEqual(scorecard.paper_buy_count, 1)
        self.assertGreaterEqual(
            scorecard.projected_entry_net_pnl,
            scorecard.target_net_pnl_usd,
        )

    def test_missing_document_fails_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing.json"
            with self.assertRaises(ExpertContractError):
                read_clip_journal_document(path)

    def test_tampered_document_fails_closed(self) -> None:
        transition, _binding, prior = trusted_home_transition()
        session = PaperClipSession.with_default_bundle(
            require_calibration=False,
            session_id="clip-store-tamper",
        )
        session.observe(transition, prior)
        records = session.journal_records()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "clip-session.json"
            write_clip_journal_document(path, records)
            raw = path.read_text(encoding="ascii")
            path.write_text(
                raw.replace('"paper_buy"', '"abstain"', 1),
                encoding="ascii",
            )
            with self.assertRaises(ExpertContractError):
                read_clip_journal_document(path)


if __name__ == "__main__":
    unittest.main()
