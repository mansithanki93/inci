"""Focused tests for the strict Models 1+2 prematch-prior adapter."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from two_model_prior import PriorDataError, TwoModelPriorStore


NOW = datetime(2026, 8, 6, 12, 0, 30, tzinfo=timezone.utc)


def _payload() -> dict:
    return {
        "schema_version": "inci-two-model-prematch-v2",
        "generated_at": "2026-08-06T12:00:00Z",
        "provenance": {
            "producer": "inci-two-model-pilot",
            "model_1_id": "inci-static-bo3-v1",
            "model_2_id": "inci-dynamic-bo3-v1",
        },
        "priors": [
            {
                "competition_id": "espn:181730",
                "athlete_id": "espn:athlete:1",
                "opponent_athlete_id": "espn:athlete:2",
                "player_name": "Ada Ace",
                "opponent_name": "Bea Break",
                "model_as_of": "2026-08-06T11:59:30Z",
                "match_start": "2026-08-06T12:01:00Z",
                "model_1_match_probability": "0.61",
                "model_2_match_probability": "0.64",
            }
        ],
    }


def _query() -> dict:
    return {
        "competition_id": "espn:181730",
        "athlete_id": "espn:athlete:1",
        "opponent_athlete_id": "espn:athlete:2",
        "player_name": "Ada Ace",
        "opponent_name": "Bea Break",
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _assert_data_error(operation, expected: str) -> None:
    try:
        operation()
    except PriorDataError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError(f"expected PriorDataError containing {expected!r}")


def test_valid_exact_lookup_returns_decimal_models_and_provenance():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        _write(path, _payload())
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: NOW)

        prior = store(**_query())

        assert prior is not None
        assert prior.model_1_probability == Decimal("0.61")
        assert prior.model_2_probability == Decimal("0.64")
        assert prior.opponent_athlete_id == "espn:athlete:2"
        assert prior.opponent_name == "Bea Break"
        assert prior.model_as_of == datetime(
            2026, 8, 6, 11, 59, 30, tzinfo=timezone.utc)
        assert prior.match_start == datetime(
            2026, 8, 6, 12, 1, tzinfo=timezone.utc)
        assert prior.provenance.schema_version == "inci-two-model-prematch-v2"
        assert prior.provenance.producer == "inci-two-model-pilot"
        assert prior.provenance.model_1_id == "inci-static-bo3-v1"
        assert prior.provenance.model_2_id == "inci-dynamic-bo3-v1"
        assert prior.provenance.generated_at == datetime(
            2026, 8, 6, 12, 0, tzinfo=timezone.utc)
        assert prior.provenance.source_path == str(path.resolve())


def test_cached_snapshot_is_rechecked_for_freshness():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        _write(path, _payload())
        clock = [NOW]
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: clock[0])
        lookup = lambda: store(**_query())
        assert lookup() is not None

        clock[0] = NOW + timedelta(seconds=31)
        _assert_data_error(lookup, "stale")


def test_lookup_requires_exact_full_identity():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        _write(path, _payload())
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: NOW)

        for field, value in (
                ("competition_id", "wrong"),
                ("athlete_id", "wrong"),
                ("opponent_athlete_id", "wrong"),
                ("player_name", "ada ace"),
                ("opponent_name", "bea break")):
            query = _query()
            query[field] = value
            assert store(**query) is None


def test_duplicate_records_and_duplicate_json_keys_are_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        payload = _payload()
        payload["priors"].append(dict(payload["priors"][0]))
        _write(path, payload)
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: NOW)
        lookup = lambda: store(**_query())
        _assert_data_error(lookup, "duplicate prior identity")

        raw = json.dumps(_payload())
        path.write_text(
            raw.replace(
                '"schema_version": "inci-two-model-prematch-v2",',
                '"schema_version": "inci-two-model-prematch-v2", '
                '"schema_version": "inci-two-model-prematch-v2",',
                1,
            ),
            encoding="utf-8",
        )
        _assert_data_error(lookup, "duplicate JSON key")


def test_malformed_nonfinite_and_numeric_probabilities_are_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: NOW)
        lookup = lambda: store(**_query())

        path.write_text("{", encoding="utf-8")
        _assert_data_error(lookup, "invalid JSON")

        numeric = _payload()
        numeric["priors"][0]["model_1_match_probability"] = 0.61
        _write(path, numeric)
        _assert_data_error(lookup, "JSON numbers are forbidden")

        nonfinite_token = _payload()
        nonfinite_token["priors"][0]["model_1_match_probability"] = float("nan")
        _write(path, nonfinite_token)
        _assert_data_error(lookup, "non-finite JSON number")

        for value in ("NaN", "Infinity", "0", "1", "-0.1", "1.1"):
            invalid = _payload()
            invalid["priors"][0]["model_1_match_probability"] = value
            _write(path, invalid)
            _assert_data_error(lookup, "model_1_match_probability")


def test_schema_provenance_and_timestamp_are_strict():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: NOW)
        lookup = lambda: store(**_query())

        wrong_schema = _payload()
        wrong_schema["schema_version"] = "v2"
        _write(path, wrong_schema)
        _assert_data_error(lookup, "schema_version")

        same_models = _payload()
        same_models["provenance"]["model_2_id"] = "inci-static-bo3-v1"
        _write(path, same_models)
        _assert_data_error(lookup, "independent")

        naive_time = _payload()
        naive_time["generated_at"] = "2026-08-06T12:00:00"
        _write(path, naive_time)
        _assert_data_error(lookup, "generated_at")

        submicrosecond = _payload()
        submicrosecond["generated_at"] = "2026-08-06T12:00:00.0000001Z"
        _write(path, submicrosecond)
        _assert_data_error(lookup, "generated_at")

        future = _payload()
        future["generated_at"] = "2026-08-06T12:01:00Z"
        _write(path, future)
        _assert_data_error(lookup, "future")

        unknown = _payload()
        unknown["unexpected"] = "unsafe schema drift"
        _write(path, unknown)
        _assert_data_error(lookup, "root fields")


def test_prior_must_be_generated_before_match_and_bind_exact_opponent():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        store = TwoModelPriorStore(path, max_age_s=60, now=lambda: NOW)
        lookup = lambda: store(**_query())

        after_start = _payload()
        after_start["priors"][0]["match_start"] = "2026-08-06T11:59:45Z"
        _write(path, after_start)
        _assert_data_error(lookup, "prematch cutoff")

        model_after_generation = _payload()
        model_after_generation["priors"][0]["model_as_of"] = \
            "2026-08-06T12:00:10Z"
        _write(path, model_after_generation)
        _assert_data_error(lookup, "model_as_of")

        same_athlete = _payload()
        same_athlete["priors"][0]["opponent_athlete_id"] = \
            "espn:athlete:1"
        _write(path, same_athlete)
        _assert_data_error(lookup, "opponent")


def test_changed_file_is_reloaded_and_environment_path_is_supported():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        _write(path, _payload())
        previous = os.environ.get("INCI_TEST_PRIOR_PATH")
        os.environ["INCI_TEST_PRIOR_PATH"] = str(path)
        try:
            store = TwoModelPriorStore.from_environment(
                "INCI_TEST_PRIOR_PATH", max_age_s=60, now=lambda: NOW)
            query = _query()
            assert store(**query).model_1_probability == Decimal("0.61")

            changed = _payload()
            changed["priors"][0]["model_1_match_probability"] = "0.721"
            _write(path, changed)
            assert store(**query).model_1_probability == Decimal("0.721")
        finally:
            if previous is None:
                os.environ.pop("INCI_TEST_PRIOR_PATH", None)
            else:
                os.environ["INCI_TEST_PRIOR_PATH"] = previous


def test_bot_builds_score_gate_with_configured_two_model_store():
    from bot import build_score_gate
    from config import Config

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priors.json"
        _write(path, _payload())
        cfg = Config(
            sports=["Tennis"],
            two_model_prior_path=str(path),
            two_model_prior_max_age_s=60,
        )

        gate = build_score_gate(cfg, prior_now=lambda: NOW)

        assert gate is not None
        assert isinstance(gate.prematch_prior_provider, TwoModelPriorStore)
        prior = gate.prematch_prior_provider(**_query())
        assert prior is not None
        assert prior.model_1_probability == Decimal("0.61")

        cfg.two_model_prior_path = ""
        assert build_score_gate(cfg) is not None
        assert build_score_gate(cfg).prematch_prior_provider is None

        cfg.espn_gate_enabled = False
        assert build_score_gate(cfg) is None


if __name__ == "__main__":
    test_valid_exact_lookup_returns_decimal_models_and_provenance()
    test_cached_snapshot_is_rechecked_for_freshness()
    test_lookup_requires_exact_full_identity()
    test_duplicate_records_and_duplicate_json_keys_are_rejected()
    test_malformed_nonfinite_and_numeric_probabilities_are_rejected()
    test_schema_provenance_and_timestamp_are_strict()
    test_prior_must_be_generated_before_match_and_bind_exact_opponent()
    test_changed_file_is_reloaded_and_environment_path_is_supported()
    test_bot_builds_score_gate_with_configured_two_model_store()
    print("ALL TWO-MODEL PRIOR TESTS PASS")
