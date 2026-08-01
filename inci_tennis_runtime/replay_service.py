"""Runtime orchestration for expert journal replay."""

from __future__ import annotations

from inci_tennis_expert.contracts import (
    BindingUniverse,
    ExpertReplayDeniedV1,
    ExpertReplayResultV1,
    SyncPolicy,
)
from inci_tennis_expert.facade import (
    begin_expert_replay,
    finish_expert_replay,
    replay_expert_parent_group,
)
from inci_tennis_io.facade import (
    abort_expert_replay_construction,
    acknowledge_begin_replay,
    acknowledge_finish_replay,
    acknowledge_parent_group_replay,
    collect_expert_current_environment,
    issue_begin_replay_authorization,
    issue_expert_environment_collection_authority,
    issue_expert_replay_construction_authority,
    issue_finish_replay_authorization,
    issue_parent_group_replay_authorization,
    prepare_expert_replay_begin,
    read_next_replay_companion_group,
    read_next_replay_evidence_parent,
    read_replay_finish_material,
    take_expert_replay_denial,
)
from inci_tennis_io.ports import (
    ExpertJournalRootAuthorityV1,
    ExpertReplayAccessDenied,
)
from tennis_v1.retention import RetentionCoordinator
from tennis_v1.sequencer import ProviderPersistenceAuthorizer


def replay_expert_session(
    *,
    authority: ExpertJournalRootAuthorityV1,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> ExpertReplayResultV1 | ExpertReplayDeniedV1:
    """Replay one expert session through the governed public facades."""

    if type(authority) is not ExpertJournalRootAuthorityV1:
        raise TypeError("authority")
    if type(persistence_authorizer) is not ProviderPersistenceAuthorizer:
        raise TypeError("persistence_authorizer")
    if type(coordinator) is not RetentionCoordinator:
        raise TypeError("coordinator")
    if type(universe) is not BindingUniverse:
        raise TypeError("universe")
    if type(policy) is not SyncPolicy:
        raise TypeError("policy")

    construction = issue_expert_replay_construction_authority(
        authority,
        persistence_authorizer=persistence_authorizer,
        coordinator=coordinator,
    )
    if type(construction) is ExpertReplayDeniedV1:
        return construction

    consumed = False
    try:
        ready = prepare_expert_replay_begin(construction)
        if type(ready) is ExpertReplayDeniedV1:
            consumed = True
            return ready

        try:
            environment_authority = (
                issue_expert_environment_collection_authority(
                    authority,
                    persistence_authorizer=persistence_authorizer,
                    coordinator=coordinator,
                )
            )
            environment = collect_expert_current_environment(
                environment_authority
            )
        except Exception:
            # Re-enter the construction authority's governed gate so any
            # access/deadline/environment loss between prepare and live
            # collection becomes a typed replay denial.  If that governed
            # gate still succeeds, preserve the original programming or I/O
            # error rather than misclassifying it.
            issue_begin_replay_authorization(construction)
            raise

        begin_authorization = issue_begin_replay_authorization(construction)
        accumulator = begin_expert_replay(
            manifest=ready.manifest,
            current_environment=environment.current,
            universe=universe,
            policy=policy,
            evidence=ready.evidence,
            authorization=begin_authorization,
        )
        acknowledge_begin_replay(
            construction,
            authorization=begin_authorization,
            accumulator=accumulator,
        )

        if accumulator.mismatch is None:
            while True:
                parent = read_next_replay_evidence_parent(construction)
                companion = read_next_replay_companion_group(construction)
                if parent is None or companion is None:
                    del parent, companion
                    break
                parent_authorization = (
                    issue_parent_group_replay_authorization(construction)
                )
                accumulator = replay_expert_parent_group(
                    accumulator,
                    authorization=parent_authorization,
                    parent=parent,
                    stored_group=companion[0],
                    stored_payloads=companion[1],
                )
                acknowledge_parent_group_replay(
                    construction,
                    authorization=parent_authorization,
                    accumulator=accumulator,
                )
                del parent, companion
                if accumulator.mismatch is not None:
                    break

        companion_terminal, companion_scan = read_replay_finish_material(
            construction
        )
        finish_authorization = issue_finish_replay_authorization(construction)
        result = finish_expert_replay(
            accumulator,
            final_authorization=finish_authorization,
            companion_terminal=companion_terminal,
            companion_scan=companion_scan,
        )
        acknowledge_finish_replay(
            construction,
            authorization=finish_authorization,
            result=result,
        )
        consumed = True
        return result
    except ExpertReplayAccessDenied:
        denial = take_expert_replay_denial(construction)
        consumed = True
        return denial
    finally:
        if not consumed:
            try:
                abort_expert_replay_construction(construction)
            except ValueError as abort_error:
                if str(abort_error) != "expert_replay_authority_invalid":
                    raise
