"""Offline validation entry point for the unregistered candidate."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence

_ARGUMENT_ERROR = "candidate observation denied: argument parsing failed\n"
_OFFLINE_DENIAL = (
    "candidate observation denied: offline validation failed\n"
)
_STARTUP_ABSENT = (
    "candidate observation unavailable: startup authority absent\n"
)
_OPTION_NAMES = (
    "--help",
    "--manifest",
    "--binding",
    "--duration-seconds",
    "--output-dir",
)
_DURATION_PATTERN = r"[1-9][0-9]{0,3}\Z"


class _RedactedArgumentParser(argparse.ArgumentParser):
    def error(self, _: str) -> None:
        self.exit(2, _ARGUMENT_ERROR)


def _duration_seconds(value: str) -> int:
    if re.fullmatch(_DURATION_PATTERN, value, flags=re.ASCII) is None:
        raise argparse.ArgumentTypeError("invalid duration")
    duration = int(value)
    if duration > 3_600:
        raise argparse.ArgumentTypeError("invalid duration")
    return duration


def _parser() -> _RedactedArgumentParser:
    parser = _RedactedArgumentParser(
        prog="qualify_sportradar_tennis_v3",
        add_help=False,
        allow_abbrev=False,
        description=(
            "Validate an external sanitized candidate request offline."
        ),
    )
    parser.add_argument(
        "--help",
        action="help",
        help="show this help message and exit",
    )
    parser.add_argument("--manifest", required=True, metavar="PATH")
    parser.add_argument("--binding", required=True, metavar="PATH")
    parser.add_argument(
        "--duration-seconds",
        required=True,
        type=_duration_seconds,
        metavar="SECONDS",
    )
    parser.add_argument("--output-dir", required=True, metavar="DIR")
    return parser


def _reject_duplicate_options(
    parser: _RedactedArgumentParser,
    arguments: Sequence[str],
) -> None:
    for name in _OPTION_NAMES:
        count = sum(
            item == name or item.startswith(name + "=")
            for item in arguments
        )
        if count > 1:
            parser.error("duplicate option")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    _reject_duplicate_options(parser, arguments)
    namespace = parser.parse_args(arguments)
    try:
        from inci_tennis_io.provider_readonly import (
            CandidateOfflineValidationError,
            sportradar_candidate_offline_is_eligible,
            validate_sportradar_candidate_offline_artifacts,
        )
    except ImportError:
        sys.stderr.write(_OFFLINE_DENIAL)
        return 4
    try:
        artifacts = validate_sportradar_candidate_offline_artifacts(
            manifest_path=namespace.manifest,
            binding_path=namespace.binding,
            duration_seconds=namespace.duration_seconds,
            output_dir=namespace.output_dir,
        )
        if not sportradar_candidate_offline_is_eligible(artifacts):
            raise CandidateOfflineValidationError()
    except CandidateOfflineValidationError:
        sys.stderr.write(_OFFLINE_DENIAL)
        return 4
    sys.stderr.write(_STARTUP_ABSENT)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
