from __future__ import annotations

from hashlib import sha256
import json
from re import ASCII as RE_ASCII
from re import compile as pattern_compile
from re import split as pattern_split
import unicodedata

from inci_tennis_expert.contracts import (
    ArtifactPin,
    BindingMarketMetadata,
    BindingMetadata,
    BindingReviewDecision,
    BindingRoute,
    BindingUniverse,
    ContractSide,
    ExpertContractError,
    MatchBinding,
    MatchFormat,
    PlayerSide,
    SettlementSemantics,
    TennisState,
    canonical_binding_review_artifact_bytes,
    compute_binding_review_artifact_sha256,
    compute_binding_review_evidence_sha256,
    compute_binding_universe_sha256,
    compute_membership_projection_sha256,
    compute_settlement_projection_sha256,
    player_side_for_contract,
)


_MANIFEST_MAX_BYTES = 1_048_576
_REVIEW_MAX_BYTES = 16_384
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_SAFE_ID_RE = pattern_compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
    RE_ASCII,
)
_TICKER_RE = pattern_compile(
    r"[A-Z0-9][A-Z0-9._-]{0,127}",
    RE_ASCII,
)
_SHA256_RE = pattern_compile(r"[0-9a-f]{64}", RE_ASCII)
_PLACEHOLDERS = frozenset(
    {
        "tbd",
        "tba",
        "unknown",
        "undecided",
        "qualifier",
        "q",
        "lucky loser",
        "ll",
        "bye",
        "player 1",
        "player 2",
        "home",
        "away",
    }
)
_ID_FIRST_PLACEHOLDERS = frozenset(
    {"tbd", "tba", "unknown", "undecided", "qualifier", "q", "ll", "bye"}
)
_DISPLAY_PREFIX_PLACEHOLDERS = (
    "winner of ",
    "loser of ",
    "qualifier ",
    "lucky loser ",
)
_SUPPORTED_FORMATS = {
    MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS.value,
    MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS.value,
}
_ROOT_KEYS = {
    "schema_version",
    "artifact_id",
    "artifact_created_wall_ns",
    "bindings",
}
_BINDING_KEYS = {
    "canonical_match_id",
    "provider",
    "competition",
    "match",
    "kalshi",
}
_PROVIDER_KEYS = {
    "sport",
    "competition_kind",
    "competitor_count",
    "source_id",
    "revision_domain_id",
    "source_lineage_sha256",
    "match_id",
    "home_player",
    "away_player",
    "scheduled_start_wall_ns",
    "snapshot_sha256",
    "snapshot_captured_wall_ns",
}
_PLAYER_KEYS = {
    "provider_player_id",
    "canonical_player_id",
    "display_name",
    "participant_type",
    "participant_status",
}
_COMPETITION_KEYS = {
    "tournament_id",
    "season_id",
    "draw_id",
    "round_id",
    "tour_id",
    "tier_id",
    "surface",
    "tournament_name",
}
_MATCH_KEYS = {
    "match_type",
    "product",
    "format",
    "start_tolerance_ns",
}
_KALSHI_KEYS = {
    "series_ticker",
    "event_ticker",
    "event_id",
    "scheduled_start_wall_ns",
    "event_sha256",
    "event_captured_wall_ns",
    "event_catalog_sha256",
    "route_authority",
    "markets",
    "authorized_routes",
}
_MARKET_KEYS = {
    "market_ticker",
    "market_id",
    "yes_player_side",
    "yes_provider_player_id",
    "yes_canonical_player_id",
    "yes_outcome",
    "membership",
    "settlement",
    "market_evidence",
}
_MEMBERSHIP_KEYS = {
    "series_ticker",
    "event_ticker",
    "event_id",
    "market_ticker",
    "market_id",
    "product",
    "event_catalog_sha256",
    "source_id",
    "source_version",
    "captured_wall_ns",
    "membership_evidence_sha256",
    "membership_projection_sha256",
}
_SETTLEMENT_KEYS = {
    "result_authority",
    "natural_completion",
    "retirement_after_point",
    "walkover_before_point",
    "default_after_point",
    "disqualification_after_point",
    "cancellation",
    "postponement",
    "abandonment",
    "amendment",
    "void_treatment",
    "raw_rules_sha256",
    "projection_sha256",
}
_EVIDENCE_KEYS = {
    "source_id",
    "source_version",
    "captured_wall_ns",
    "market_text",
    "market_text_sha256",
    "settlement_rule_text",
    "settlement_rule_text_sha256",
}
_ROUTE_KEYS = {"player_side", "market_ticker", "contract_side"}
_REVIEW_KEYS = {
    "schema_version",
    "artifact_id",
    "artifact_created_wall_ns",
    "binding_artifact_id",
    "binding_artifact_sha256",
    "decision",
    "reviewer_id",
    "reviewed_wall_ns",
    "review_evidence_sha256",
}


class _DuplicateKey(Exception):
    pass


class _JsonNumber(Exception):
    pass


def _raise_number(_: str) -> object:
    raise _JsonNumber


def _parse_integer(token: str) -> int:
    if len(token.removeprefix("-")) > 19:
        raise _JsonNumber
    try:
        return int(token)
    except (ValueError, OverflowError) as error:
        raise _JsonNumber from None


def _object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _decode_json(payload: bytes, domain: str) -> object:
    prefix = f"binding_{domain}_json_"
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ExpertContractError(prefix + "bom")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ExpertContractError(prefix + "utf8") from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_float=_raise_number,
            parse_int=_parse_integer,
            parse_constant=_raise_number,
        )
    except _DuplicateKey as error:
        raise ExpertContractError(prefix + "duplicate_key") from None
    except _JsonNumber as error:
        raise ExpertContractError(prefix + "number") from None
    except RecursionError as error:
        raise ExpertContractError(prefix + "recursion") from None
    except (ValueError, OverflowError) as error:
        if type(error) is json.JSONDecodeError:
            raise ExpertContractError(prefix + "syntax") from None
        raise ExpertContractError(prefix + "number") from None


def _exact(value: object, cls: type[object], name: str) -> None:
    if type(value) is not cls:
        raise TypeError(name)


def _shape_object(
    value: object,
    keys: set[str],
    *,
    error: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ExpertContractError(error)
    return value


def _shape_list(
    value: object,
    *,
    error: str,
    length: int | None = None,
) -> list[object]:
    if type(value) is not list or (
        length is not None and len(value) != length
    ):
        raise ExpertContractError(error)
    return value


def _integer(value: object, *, error: str, maximum: int = _MAX_SIGNED_64) -> int:
    if type(value) is not int:
        raise ExpertContractError(_shape_error(error))
    if value < 0 or value > maximum:
        raise ExpertContractError(error)
    return value


def _string(value: object, *, error: str) -> str:
    if type(value) is not str:
        raise ExpertContractError(_shape_error(error))
    return value


def _shape_error(error: str) -> str:
    if error == "binding_manifest_value":
        return "binding_manifest_shape"
    if error == "binding_review_value":
        return "binding_review_shape"
    return error


def _safe_id(value: object, *, error: str) -> str:
    text = _string(value, error=error)
    if (
        _SAFE_ID_RE.fullmatch(text) is None
        or text.lower().startswith(("http:", "https:", "file:"))
    ):
        raise ExpertContractError(error)
    return text


def _ticker(value: object, *, error: str) -> str:
    text = _string(value, error=error)
    if _TICKER_RE.fullmatch(text) is None:
        raise ExpertContractError(error)
    return text


def _digest(value: object, *, error: str) -> str:
    text = _string(value, error=error)
    if _SHA256_RE.fullmatch(text) is None:
        raise ExpertContractError(error)
    return text


def _display_text(
    value: object,
    *,
    error: str,
    max_bytes: int,
    multiline: bool = False,
) -> str:
    text = _string(value, error=error)
    for character in text:
        if 0xD800 <= ord(character) <= 0xDFFF:
            raise ExpertContractError(error)
    encoded = text.encode("utf-8")
    if (
        not encoded
        or len(encoded) > max_bytes
        or unicodedata.normalize("NFC", text) != text
    ):
        raise ExpertContractError(error)
    if multiline:
        if "\r" in text:
            raise ExpertContractError(error)
        for character in text:
            if (
                0xD800 <= ord(character) <= 0xDFFF
                or (
                    unicodedata.category(character) == "Cc"
                    and character not in {"\n", "\t"}
                )
            ):
                raise ExpertContractError(error)
    else:
        if text.strip() != text:
            raise ExpertContractError(error)
        for character in text:
            if (
                0xD800 <= ord(character) <= 0xDFFF
                or unicodedata.category(character) == "Cc"
            ):
                raise ExpertContractError(error)
    return text


def _placeholder_form(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _reject_placeholder(value: str, *, display: bool) -> None:
    normalized = _placeholder_form(value)
    if normalized in _PLACEHOLDERS:
        raise ExpertContractError("binding_manifest_placeholder")
    if display and normalized.startswith(_DISPLAY_PREFIX_PLACEHOLDERS):
        raise ExpertContractError("binding_manifest_placeholder")
    if not display:
        tokens = tuple(
            token
            for token in pattern_split(r"[._:-]+", normalized)
            if token
        )
        if (
            (tokens and tokens[0] in _ID_FIRST_PLACEHOLDERS)
            or tokens[:2] in {("player", "1"), ("player", "2")}
        ):
            raise ExpertContractError("binding_manifest_placeholder")


def _market_semantics(
    raw: dict[str, object],
) -> SettlementSemantics:
    _shape_object(
        raw,
        _SETTLEMENT_KEYS,
        error="binding_manifest_shape",
    )
    string_names = (
        "result_authority",
        "natural_completion",
        "retirement_after_point",
        "walkover_before_point",
        "default_after_point",
        "disqualification_after_point",
        "cancellation",
        "postponement",
        "abandonment",
        "amendment",
        "void_treatment",
    )
    for name in string_names:
        _string(raw[name], error="binding_manifest_value")
    _digest(raw["raw_rules_sha256"], error="binding_manifest_value")
    _digest(raw["projection_sha256"], error="binding_manifest_value")
    try:
        projected = compute_settlement_projection_sha256(
            result_authority=raw["result_authority"],  # type: ignore[arg-type]
            natural_completion=raw["natural_completion"],  # type: ignore[arg-type]
            retirement_after_point=raw["retirement_after_point"],  # type: ignore[arg-type]
            walkover_before_point=raw["walkover_before_point"],  # type: ignore[arg-type]
            default_after_point=raw["default_after_point"],  # type: ignore[arg-type]
            disqualification_after_point=raw["disqualification_after_point"],  # type: ignore[arg-type]
            cancellation=raw["cancellation"],  # type: ignore[arg-type]
            postponement=raw["postponement"],  # type: ignore[arg-type]
            abandonment=raw["abandonment"],  # type: ignore[arg-type]
            amendment=raw["amendment"],  # type: ignore[arg-type]
            void_treatment=raw["void_treatment"],  # type: ignore[arg-type]
            raw_rules_sha256=raw["raw_rules_sha256"],  # type: ignore[arg-type]
        )
    except (TypeError, ExpertContractError) as error:
        raise ExpertContractError("binding_manifest_value") from None
    if projected != raw["projection_sha256"]:
        raise ExpertContractError("binding_manifest_evidence")
    try:
        return SettlementSemantics(**raw)  # type: ignore[arg-type]
    except (TypeError, ExpertContractError) as error:
        raise ExpertContractError("binding_projection") from None


def _parse_player(raw: object) -> dict[str, object]:
    player = _shape_object(
        raw,
        _PLAYER_KEYS,
        error="binding_manifest_shape",
    )
    provider_id = _safe_id(
        player["provider_player_id"],
        error="binding_manifest_value",
    )
    _reject_placeholder(provider_id, display=False)
    canonical_id = _safe_id(
        player["canonical_player_id"],
        error="binding_manifest_value",
    )
    _reject_placeholder(canonical_id, display=False)
    display_name = _display_text(
        player["display_name"],
        error="binding_manifest_value",
        max_bytes=256,
    )
    _reject_placeholder(display_name, display=True)
    participant_type = _string(
        player["participant_type"],
        error="binding_manifest_value",
    )
    participant_status = _string(
        player["participant_status"],
        error="binding_manifest_value",
    )
    if (
        participant_type != "player"
        or participant_status != "confirmed"
    ):
        raise ExpertContractError("binding_manifest_value")
    return player


def _parse_binding(
    raw: object,
    *,
    artifact_created_wall_ns: int,
    manifest_pin: ArtifactPin,
) -> tuple[
    MatchBinding,
    BindingMetadata,
    int,
    tuple[tuple[str, str, str, str], ...],
]:
    binding = _shape_object(
        raw,
        _BINDING_KEYS,
        error="binding_manifest_shape",
    )
    canonical_match_id = _safe_id(
        binding["canonical_match_id"],
        error="binding_manifest_value",
    )
    provider = _shape_object(
        binding["provider"],
        _PROVIDER_KEYS,
        error="binding_manifest_shape",
    )
    sport = _string(provider["sport"], error="binding_manifest_value")
    competition_kind = _string(
        provider["competition_kind"],
        error="binding_manifest_value",
    )
    competitor_count = _integer(
        provider["competitor_count"],
        error="binding_manifest_value",
    )
    if (
        sport != "tennis"
        or competition_kind != "real"
        or competitor_count != 2
    ):
        raise ExpertContractError("binding_manifest_value")
    provider_source_id = _safe_id(
        provider["source_id"],
        error="binding_manifest_value",
    )
    revision_domain_id = _safe_id(
        provider["revision_domain_id"],
        error="binding_manifest_value",
    )
    source_lineage_sha256 = _digest(
        provider["source_lineage_sha256"],
        error="binding_manifest_value",
    )
    provider_match_id = _safe_id(
        provider["match_id"],
        error="binding_manifest_value",
    )
    home_player = _parse_player(provider["home_player"])
    away_player = _parse_player(provider["away_player"])
    if (
        home_player["provider_player_id"]
        == away_player["provider_player_id"]
        or home_player["canonical_player_id"]
        == away_player["canonical_player_id"]
    ):
        raise ExpertContractError("binding_manifest_value")
    if _placeholder_form(home_player["display_name"]) == _placeholder_form(  # type: ignore[arg-type]
        away_player["display_name"]  # type: ignore[arg-type]
    ):
        raise ExpertContractError("binding_manifest_placeholder")
    provider_start = _integer(
        provider["scheduled_start_wall_ns"],
        error="binding_manifest_value",
    )
    provider_snapshot_sha = _digest(
        provider["snapshot_sha256"],
        error="binding_manifest_value",
    )
    provider_capture = _integer(
        provider["snapshot_captured_wall_ns"],
        error="binding_manifest_value",
    )

    competition = _shape_object(
        binding["competition"],
        _COMPETITION_KEYS,
        error="binding_manifest_shape",
    )
    competition_ids = {}
    for name in (
        "tournament_id",
        "season_id",
        "draw_id",
        "round_id",
        "tour_id",
        "tier_id",
    ):
        competition_ids[name] = _safe_id(
            competition[name],
            error="binding_manifest_value",
        )
    surface = _string(
        competition["surface"],
        error="binding_manifest_value",
    )
    if surface not in {"hard", "clay", "grass", "carpet"}:
        raise ExpertContractError("binding_manifest_value")
    _display_text(
        competition["tournament_name"],
        error="binding_manifest_value",
        max_bytes=256,
    )

    match = _shape_object(
        binding["match"],
        _MATCH_KEYS,
        error="binding_manifest_shape",
    )
    match_type = _string(
        match["match_type"],
        error="binding_manifest_value",
    )
    match_product = _string(
        match["product"],
        error="binding_manifest_value",
    )
    if match_type != "singles" or match_product != "match_winner":
        raise ExpertContractError("binding_manifest_value")
    match_format_text = _string(
        match["format"],
        error="binding_manifest_value",
    )
    if match_format_text not in _SUPPORTED_FORMATS:
        raise ExpertContractError("binding_manifest_value")
    start_tolerance = _integer(
        match["start_tolerance_ns"],
        error="binding_manifest_value",
        maximum=900_000_000_000,
    )

    kalshi = _shape_object(
        binding["kalshi"],
        _KALSHI_KEYS,
        error="binding_manifest_shape",
    )
    series_ticker = _ticker(
        kalshi["series_ticker"],
        error="binding_manifest_value",
    )
    event_ticker = _ticker(
        kalshi["event_ticker"],
        error="binding_manifest_value",
    )
    event_id = _safe_id(
        kalshi["event_id"],
        error="binding_manifest_value",
    )
    kalshi_start = _integer(
        kalshi["scheduled_start_wall_ns"],
        error="binding_manifest_value",
    )
    event_sha = _digest(
        kalshi["event_sha256"],
        error="binding_manifest_value",
    )
    event_capture = _integer(
        kalshi["event_captured_wall_ns"],
        error="binding_manifest_value",
    )
    event_catalog_sha = _digest(
        kalshi["event_catalog_sha256"],
        error="binding_manifest_value",
    )
    route_authority = _string(
        kalshi["route_authority"],
        error="binding_manifest_value",
    )
    if route_authority != "direct_yes_only":
        raise ExpertContractError("binding_manifest_route")
    raw_markets = _shape_list(
        kalshi["markets"],
        error="binding_manifest_shape",
        length=2,
    )
    projected_markets: list[BindingMarketMetadata] = []
    market_evidence_captures: list[int] = []
    expected_players = (
        (
            PlayerSide.HOME,
            home_player["provider_player_id"],
            home_player["canonical_player_id"],
        ),
        (
            PlayerSide.AWAY,
            away_player["provider_player_id"],
            away_player["canonical_player_id"],
        ),
    )
    for index, raw_market_value in enumerate(raw_markets):
        raw_market = _shape_object(
            raw_market_value,
            _MARKET_KEYS,
            error="binding_manifest_shape",
        )
        market_ticker = _ticker(
            raw_market["market_ticker"],
            error="binding_manifest_value",
        )
        market_id = _safe_id(
            raw_market["market_id"],
            error="binding_manifest_value",
        )
        expected_side, expected_provider, expected_canonical = (
            expected_players[index]
        )
        yes_player_side = _string(
            raw_market["yes_player_side"],
            error="binding_manifest_value",
        )
        yes_provider_player_id = _string(
            raw_market["yes_provider_player_id"],
            error="binding_manifest_value",
        )
        yes_canonical_player_id = _string(
            raw_market["yes_canonical_player_id"],
            error="binding_manifest_value",
        )
        yes_outcome = _string(
            raw_market["yes_outcome"],
            error="binding_manifest_value",
        )
        if (
            yes_player_side != expected_side.value
            or yes_provider_player_id != expected_provider
            or yes_canonical_player_id != expected_canonical
            or yes_outcome != "wins_match"
        ):
            raise ExpertContractError("binding_manifest_route")
        membership = _shape_object(
            raw_market["membership"],
            _MEMBERSHIP_KEYS,
            error="binding_manifest_shape",
        )
        membership_fields = (
            ("series_ticker", series_ticker),
            ("event_ticker", event_ticker),
            ("event_id", event_id),
            ("market_ticker", market_ticker),
            ("market_id", market_id),
            ("product", "match_winner"),
            ("event_catalog_sha256", event_catalog_sha),
        )
        for field, expected in membership_fields:
            if type(membership[field]) is not str:
                raise ExpertContractError("binding_manifest_shape")
            if membership[field] != expected:
                raise ExpertContractError("binding_manifest_evidence")
        source_id = _safe_id(
            membership["source_id"],
            error="binding_manifest_value",
        )
        source_version = _safe_id(
            membership["source_version"],
            error="binding_manifest_value",
        )
        membership_capture = _integer(
            membership["captured_wall_ns"],
            error="binding_manifest_value",
        )
        membership_evidence_sha = _digest(
            membership["membership_evidence_sha256"],
            error="binding_manifest_value",
        )
        membership_projection_sha = _digest(
            membership["membership_projection_sha256"],
            error="binding_manifest_value",
        )
        computed_membership = compute_membership_projection_sha256(
            series_ticker=series_ticker,
            event_ticker=event_ticker,
            event_id=event_id,
            market_ticker=market_ticker,
            market_id=market_id,
            product="match_winner",
            event_catalog_sha256=event_catalog_sha,
            membership_source_id=source_id,
            membership_source_version=source_version,
            membership_captured_wall_ns=membership_capture,
            membership_evidence_sha256=membership_evidence_sha,
        )
        if membership_projection_sha != computed_membership:
            raise ExpertContractError("binding_manifest_evidence")
        settlement = _market_semantics(
            _shape_object(
                raw_market["settlement"],
                _SETTLEMENT_KEYS,
                error="binding_manifest_shape",
            )
        )
        evidence = _shape_object(
            raw_market["market_evidence"],
            _EVIDENCE_KEYS,
            error="binding_manifest_shape",
        )
        _safe_id(evidence["source_id"], error="binding_manifest_value")
        _safe_id(
            evidence["source_version"],
            error="binding_manifest_value",
        )
        evidence_capture = _integer(
            evidence["captured_wall_ns"],
            error="binding_manifest_value",
        )
        market_text = _display_text(
            evidence["market_text"],
            error="binding_manifest_value",
            max_bytes=8_192,
            multiline=True,
        )
        market_text_sha = _digest(
            evidence["market_text_sha256"],
            error="binding_manifest_value",
        )
        settlement_text = _display_text(
            evidence["settlement_rule_text"],
            error="binding_manifest_value",
            max_bytes=32_768,
            multiline=True,
        )
        settlement_text_sha = _digest(
            evidence["settlement_rule_text_sha256"],
            error="binding_manifest_value",
        )
        if (
            sha256(market_text.encode("utf-8")).hexdigest()
            != market_text_sha
            or sha256(settlement_text.encode("utf-8")).hexdigest()
            != settlement_text_sha
            or settlement.raw_rules_sha256 != settlement_text_sha
            or membership_capture > artifact_created_wall_ns
        ):
            raise ExpertContractError("binding_manifest_evidence")
        market_evidence_captures.extend(
            (membership_capture, evidence_capture)
        )
        try:
            projected_markets.append(
                BindingMarketMetadata(
                    series_ticker=series_ticker,
                    event_ticker=event_ticker,
                    event_id=event_id,
                    market_ticker=market_ticker,
                    market_id=market_id,
                    yes_player_side=expected_side,
                    yes_provider_player_id=expected_provider,  # type: ignore[arg-type]
                    yes_canonical_player_id=expected_canonical,  # type: ignore[arg-type]
                    product="match_winner",
                    event_catalog_sha256=event_catalog_sha,
                    membership_source_id=source_id,
                    membership_source_version=source_version,
                    membership_captured_wall_ns=membership_capture,
                    membership_evidence_sha256=membership_evidence_sha,
                    membership_projection_sha256=(
                        membership_projection_sha
                    ),
                    market_text_sha256=market_text_sha,
                    settlement_rule_text_sha256=settlement_text_sha,
                    settlement=settlement,
                )
            )
        except (TypeError, ExpertContractError) as error:
            raise ExpertContractError("binding_projection") from None
    raw_routes = _shape_list(
        kalshi["authorized_routes"],
        error="binding_manifest_shape",
    )
    if len(raw_routes) != 2:
        raise ExpertContractError("binding_manifest_route")
    expected_route_values = (
        (
            "home",
            projected_markets[0].market_ticker,
            "yes",
        ),
        (
            "away",
            projected_markets[1].market_ticker,
            "yes",
        ),
    )
    routes: list[BindingRoute] = []
    for raw_route_value, expected in zip(
        raw_routes,
        expected_route_values,
        strict=True,
    ):
        raw_route = _shape_object(
            raw_route_value,
            _ROUTE_KEYS,
            error="binding_manifest_shape",
        )
        actual = (
            _string(
                raw_route["player_side"],
                error="binding_manifest_value",
            ),
            _string(
                raw_route["market_ticker"],
                error="binding_manifest_value",
            ),
            _string(
                raw_route["contract_side"],
                error="binding_manifest_value",
            ),
        )
        if actual != expected:
            raise ExpertContractError("binding_manifest_route")
        routes.append(
            BindingRoute(
                PlayerSide(raw_route["player_side"]),  # type: ignore[arg-type]
                raw_route["market_ticker"],  # type: ignore[arg-type]
                ContractSide(raw_route["contract_side"]),  # type: ignore[arg-type]
            )
        )
    if abs(provider_start - kalshi_start) > start_tolerance:
        raise ExpertContractError("binding_manifest_start_time")
    if (
        artifact_created_wall_ns > provider_start
        or artifact_created_wall_ns > kalshi_start
        or provider_capture > artifact_created_wall_ns
        or event_capture > artifact_created_wall_ns
        or any(
            captured > artifact_created_wall_ns
            for captured in market_evidence_captures
        )
    ):
        raise ExpertContractError("binding_manifest_evidence")
    try:
        slim = MatchBinding(
            provider_match_id=provider_match_id,
            canonical_match_id=canonical_match_id,
            provider_source_id=provider_source_id,
            revision_domain_id=revision_domain_id,
            source_lineage_sha256=source_lineage_sha256,
            provider_home_player_id=home_player["provider_player_id"],  # type: ignore[arg-type]
            provider_away_player_id=away_player["provider_player_id"],  # type: ignore[arg-type]
            kalshi_event_ticker=event_ticker,
            home_market_ticker=projected_markets[0].market_ticker,
            away_market_ticker=projected_markets[1].market_ticker,
            match_format=MatchFormat(match_format_text),
            scheduled_start_wall_ns=provider_start,
            start_tolerance_ns=start_tolerance,
            artifact_created_wall_ns=artifact_created_wall_ns,
            binding_artifact_sha256=manifest_pin.artifact_sha256,
        )
        metadata = BindingMetadata(
            canonical_match_id=canonical_match_id,
            canonical_home_player_id=home_player["canonical_player_id"],  # type: ignore[arg-type]
            canonical_away_player_id=away_player["canonical_player_id"],  # type: ignore[arg-type]
            tournament_id=competition_ids["tournament_id"],
            season_id=competition_ids["season_id"],
            draw_id=competition_ids["draw_id"],
            round_id=competition_ids["round_id"],
            tour_id=competition_ids["tour_id"],
            tier_id=competition_ids["tier_id"],
            surface=surface,
            provider_snapshot_sha256=provider_snapshot_sha,
            kalshi_event_sha256=event_sha,
            markets=tuple(projected_markets),
            authorized_routes=tuple(routes),
        )
    except (TypeError, ValueError) as error:
        raise ExpertContractError("binding_projection") from None
    occurrences = (
        (
            provider_source_id,
            source_lineage_sha256,
            slim.provider_home_player_id,
            metadata.canonical_home_player_id,
        ),
        (
            provider_source_id,
            source_lineage_sha256,
            slim.provider_away_player_id,
            metadata.canonical_away_player_id,
        ),
    )
    return slim, metadata, kalshi_start, occurrences


def _validate_global_identity(
    bindings: tuple[MatchBinding, ...],
    metadata: tuple[BindingMetadata, ...],
    occurrences: tuple[tuple[str, str, str, str], ...],
) -> None:
    previous: tuple[str, str, str, str, str] | None = None
    seen_domains: tuple[set[object], ...] = (
        set(),
        set(),
        set(),
        set(),
        set(),
        set(),
    )
    for binding, item in zip(bindings, metadata, strict=True):
        key = (
            binding.canonical_match_id,
            binding.provider_source_id,
            binding.revision_domain_id,
            binding.provider_match_id,
            binding.kalshi_event_ticker,
        )
        if previous is not None and key <= previous:
            raise ExpertContractError("binding_manifest_order")
        previous = key
        singular = (
            binding.canonical_match_id,
            (
                binding.provider_source_id,
                binding.revision_domain_id,
                binding.provider_match_id,
            ),
            binding.kalshi_event_ticker,
            item.markets[0].event_id,
        )
        for seen, value in zip(
            seen_domains[:4],
            singular,
            strict=True,
        ):
            if value in seen:
                raise ExpertContractError("binding_manifest_collision")
            seen.add(value)
        for market_ticker in (
            item.markets[0].market_ticker,
            item.markets[1].market_ticker,
        ):
            if market_ticker in seen_domains[4]:
                raise ExpertContractError("binding_manifest_collision")
            seen_domains[4].add(market_ticker)
        for market_id in (
            item.markets[0].market_id,
            item.markets[1].market_id,
        ):
            if market_id in seen_domains[5]:
                raise ExpertContractError("binding_manifest_collision")
            seen_domains[5].add(market_id)
    provider_map: dict[tuple[str, str, str], str] = {}
    for source, lineage, provider, canonical in occurrences:
        key = (source, lineage, provider)
        existing = provider_map.get(key)
        if existing is not None and existing != canonical:
            raise ExpertContractError(
                "binding_manifest_provider_player_collision"
            )
        provider_map[key] = canonical
    canonical_map: dict[tuple[str, str, str], str] = {}
    for source, lineage, provider, canonical in occurrences:
        key = (source, lineage, canonical)
        existing = canonical_map.get(key)
        if existing is not None and existing != provider:
            raise ExpertContractError(
                "binding_manifest_canonical_player_collision"
            )
        canonical_map[key] = provider


def _parse_manifest(
    value: object,
    manifest_pin: ArtifactPin,
) -> tuple[
    int,
    tuple[MatchBinding, ...],
    tuple[BindingMetadata, ...],
    tuple[int, ...],
]:
    root = _shape_object(
        value,
        _ROOT_KEYS,
        error="binding_manifest_shape",
    )
    if type(root["schema_version"]) is not int:
        raise ExpertContractError("binding_manifest_shape")
    if root["schema_version"] != 1:
        raise ExpertContractError("binding_manifest_value")
    artifact_id = _safe_id(
        root["artifact_id"],
        error="binding_manifest_value",
    )
    if artifact_id != manifest_pin.artifact_id:
        raise ExpertContractError("binding_manifest_value")
    created = _integer(
        root["artifact_created_wall_ns"],
        error="binding_manifest_value",
    )
    raw_bindings = _shape_list(
        root["bindings"],
        error="binding_manifest_shape",
    )
    if len(raw_bindings) < 1 or len(raw_bindings) > 128:
        raise ExpertContractError("binding_manifest_value")
    bindings: list[MatchBinding] = []
    metadata: list[BindingMetadata] = []
    kalshi_starts: list[int] = []
    occurrences: list[tuple[str, str, str, str]] = []
    for raw in raw_bindings:
        slim, item, kalshi_start, item_occurrences = _parse_binding(
            raw,
            artifact_created_wall_ns=created,
            manifest_pin=manifest_pin,
        )
        bindings.append(slim)
        metadata.append(item)
        kalshi_starts.append(kalshi_start)
        occurrences.extend(item_occurrences)
    binding_tuple = tuple(bindings)
    metadata_tuple = tuple(metadata)
    _validate_global_identity(
        binding_tuple,
        metadata_tuple,
        tuple(occurrences),
    )
    return created, binding_tuple, metadata_tuple, tuple(kalshi_starts)


def _parse_review(
    value: object,
    payload: bytes,
    *,
    review_pin: ArtifactPin,
    manifest_pin: ArtifactPin,
    review_evidence_sha256: str,
    artifact_created_wall_ns: int,
    bindings: tuple[MatchBinding, ...],
    metadata: tuple[BindingMetadata, ...],
    kalshi_starts: tuple[int, ...],
) -> BindingReviewDecision:
    root = _shape_object(
        value,
        _REVIEW_KEYS,
        error="binding_review_shape",
    )
    if type(root["schema_version"]) is not int:
        raise ExpertContractError("binding_review_shape")
    if root["schema_version"] != 1:
        raise ExpertContractError("binding_review_value")
    artifact_id = _safe_id(
        root["artifact_id"],
        error="binding_review_value",
    )
    artifact_created = _integer(
        root["artifact_created_wall_ns"],
        error="binding_review_value",
    )
    binding_artifact_id = _safe_id(
        root["binding_artifact_id"],
        error="binding_review_value",
    )
    binding_artifact_sha = _digest(
        root["binding_artifact_sha256"],
        error="binding_review_value",
    )
    decision = _string(root["decision"], error="binding_review_value")
    reviewer_id = _safe_id(
        root["reviewer_id"],
        error="binding_review_value",
    )
    reviewed = _integer(
        root["reviewed_wall_ns"],
        error="binding_review_value",
    )
    evidence = _digest(
        root["review_evidence_sha256"],
        error="binding_review_value",
    )
    if (
        artifact_id != review_pin.artifact_id
        or binding_artifact_id != manifest_pin.artifact_id
        or binding_artifact_sha != manifest_pin.artifact_sha256
    ):
        raise ExpertContractError("binding_review_artifact")
    if decision != "approved":
        raise ExpertContractError("binding_review_value")
    if evidence != review_evidence_sha256:
        raise ExpertContractError("binding_review_evidence")
    canonical = canonical_binding_review_artifact_bytes(
        review_artifact_id=artifact_id,
        review_artifact_created_wall_ns=artifact_created,
        binding_artifact_id=binding_artifact_id,
        binding_artifact_sha256=binding_artifact_sha,
        decision=decision,
        reviewer_id=reviewer_id,
        reviewed_wall_ns=reviewed,
        review_evidence_sha256=evidence,
    )
    computed_review_sha = compute_binding_review_artifact_sha256(
        review_artifact_id=artifact_id,
        review_artifact_created_wall_ns=artifact_created,
        binding_artifact_id=binding_artifact_id,
        binding_artifact_sha256=binding_artifact_sha,
        decision=decision,
        reviewer_id=reviewer_id,
        reviewed_wall_ns=reviewed,
        review_evidence_sha256=evidence,
    )
    if (
        payload != canonical
        or computed_review_sha != review_pin.artifact_sha256
    ):
        raise ExpertContractError("binding_review_artifact")
    if (
        reviewed < artifact_created_wall_ns
        or artifact_created > min(
            tuple(binding.scheduled_start_wall_ns for binding in bindings)
            + kalshi_starts
        )
        or reviewed > artifact_created
    ):
        raise ExpertContractError("binding_review_time")
    for binding, item in zip(bindings, metadata, strict=True):
        if (
            any(
                market.membership_captured_wall_ns
                > binding.artifact_created_wall_ns
                for market in item.markets
            )
            or binding.artifact_created_wall_ns > reviewed
        ):
            raise ExpertContractError("binding_review_time")
    try:
        return BindingReviewDecision(
            review_artifact_id=artifact_id,
            review_artifact_sha256=review_pin.artifact_sha256,
            review_artifact_created_wall_ns=artifact_created,
            binding_artifact_id=binding_artifact_id,
            binding_artifact_sha256=binding_artifact_sha,
            decision=decision,
            reviewer_id=reviewer_id,
            reviewed_wall_ns=reviewed,
            review_evidence_sha256=evidence,
        )
    except (TypeError, ValueError) as error:
        raise ExpertContractError("binding_projection") from None


def decode_binding_universe(
    manifest_payload: bytes,
    review_payload: bytes,
    *,
    manifest_pin: ArtifactPin,
    review_pin: ArtifactPin,
) -> BindingUniverse:
    _exact(manifest_payload, bytes, "manifest_payload")
    _exact(review_payload, bytes, "review_payload")
    _exact(manifest_pin, ArtifactPin, "manifest_pin")
    _exact(review_pin, ArtifactPin, "review_pin")
    if len(manifest_payload) < 1 or len(manifest_payload) > _MANIFEST_MAX_BYTES:
        raise ExpertContractError("binding_manifest_payload_size")
    if len(review_payload) < 1 or len(review_payload) > _REVIEW_MAX_BYTES:
        raise ExpertContractError("binding_review_payload_size")
    if sha256(manifest_payload).hexdigest() != manifest_pin.artifact_sha256:
        raise ExpertContractError("binding_manifest_payload_sha256")
    if sha256(review_payload).hexdigest() != review_pin.artifact_sha256:
        raise ExpertContractError("binding_review_payload_sha256")
    manifest_value = _decode_json(manifest_payload, "manifest")
    (
        artifact_created,
        bindings,
        metadata,
        kalshi_starts,
    ) = _parse_manifest(manifest_value, manifest_pin)
    review_evidence_sha256 = compute_binding_review_evidence_sha256(
        manifest_pin,
        bindings,
        metadata,
    )
    review_value = _decode_json(review_payload, "review")
    review = _parse_review(
        review_value,
        review_payload,
        review_pin=review_pin,
        manifest_pin=manifest_pin,
        review_evidence_sha256=review_evidence_sha256,
        artifact_created_wall_ns=artifact_created,
        bindings=bindings,
        metadata=metadata,
        kalshi_starts=kalshi_starts,
    )
    universe_sha256 = compute_binding_universe_sha256(
        manifest_pin,
        review,
        bindings,
        metadata,
    )
    try:
        return BindingUniverse(
            raw_artifact_id=manifest_pin.artifact_id,
            raw_artifact_sha256=manifest_pin.artifact_sha256,
            review=review,
            bindings=bindings,
            metadata=metadata,
            universe_sha256=universe_sha256,
        )
    except (TypeError, ValueError) as error:
        raise ExpertContractError("binding_projection") from None


def _require_universe(universe: BindingUniverse) -> None:
    _exact(universe, BindingUniverse, "universe")
    BindingUniverse.__post_init__(universe)


def binding_universe_sha256(universe: BindingUniverse) -> str:
    _require_universe(universe)
    computed = compute_binding_universe_sha256(
        ArtifactPin(
            universe.raw_artifact_id,
            universe.raw_artifact_sha256,
        ),
        universe.review,
        universe.bindings,
        universe.metadata,
    )
    if computed != universe.universe_sha256:
        raise ExpertContractError("universe_sha256")
    return computed


def binding_metadata_for(
    universe: BindingUniverse,
    binding: MatchBinding,
) -> BindingMetadata:
    _exact(universe, BindingUniverse, "universe")
    _exact(binding, MatchBinding, "binding")
    _require_universe(universe)
    matches = tuple(
        index
        for index, candidate in enumerate(universe.bindings)
        if candidate == binding
    )
    if len(matches) != 1:
        raise ExpertContractError("binding_not_in_universe")
    return universe.metadata[matches[0]]


def resolve_binding(
    provider_state: TennisState,
    kalshi_event_ticker: str,
    universe: BindingUniverse,
) -> MatchBinding:
    _exact(provider_state, TennisState, "provider_state")
    if type(kalshi_event_ticker) is not str:
        raise TypeError("kalshi_event_ticker")
    _exact(universe, BindingUniverse, "universe")
    _ticker(
        kalshi_event_ticker,
        error="kalshi_event_ticker",
    )
    TennisState.__post_init__(provider_state)
    _require_universe(universe)
    matches = tuple(
        binding
        for binding in universe.bindings
        if (
            binding.provider_source_id
            == provider_state.provider_source_id
            and binding.revision_domain_id
            == provider_state.revision_domain_id
            and binding.source_lineage_sha256
            == provider_state.source_lineage_sha256
            and binding.provider_match_id
            == provider_state.provider_match_id
            and binding.provider_home_player_id
            == provider_state.home_player_id
            and binding.provider_away_player_id
            == provider_state.away_player_id
            and binding.match_format is provider_state.match_format
            and binding.scheduled_start_wall_ns
            == provider_state.scheduled_start_wall_ns
            and binding.kalshi_event_ticker == kalshi_event_ticker
        )
    )
    if len(matches) != 1:
        raise ExpertContractError("binding_not_found")
    return matches[0]


def require_authorized_route(
    universe: BindingUniverse,
    binding: MatchBinding,
    market_ticker: str,
    contract_side: ContractSide,
) -> BindingRoute:
    _exact(universe, BindingUniverse, "universe")
    _exact(binding, MatchBinding, "binding")
    if type(market_ticker) is not str:
        raise TypeError("market_ticker")
    _exact(contract_side, ContractSide, "contract_side")
    _require_universe(universe)
    metadata = binding_metadata_for(universe, binding)
    _ticker(market_ticker, error="market_ticker")
    for route in metadata.authorized_routes:
        if (
            route.market_ticker == market_ticker
            and route.contract_side is contract_side
        ):
            return route
    raise ExpertContractError("binding_route_unsupported")
