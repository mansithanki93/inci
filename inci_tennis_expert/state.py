from __future__ import annotations

from .contracts import (
    BindingUniverse,
    ExpertContractError,
    ExpertSessionManifestV1,
    ExpertStateV1,
    SyncPolicy,
    canonical_expert_bytes,
    expert_contract_sha256,
)
from .match_binding import binding_universe_sha256
from .synchronizer import synchronization_session_from_artifacts


__all__ = ("initial_expert_state",)


def initial_expert_state(
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> ExpertStateV1:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    if type(universe) is not BindingUniverse:
        raise TypeError("universe")
    if type(policy) is not SyncPolicy:
        raise TypeError("policy")
    ExpertSessionManifestV1.__post_init__(manifest)
    BindingUniverse.__post_init__(universe)
    SyncPolicy.__post_init__(policy)
    universe_sha256 = binding_universe_sha256(universe)
    policy_sha256 = expert_contract_sha256(policy)
    if (
        universe_sha256 != manifest.match_binding_universe_sha256
        or policy.universe_sha256 != universe_sha256
        or policy_sha256 != manifest.sync_policy_sha256
    ):
        raise ExpertContractError("initial_expert_artifacts")
    synchronization = synchronization_session_from_artifacts(universe, policy)
    initial_sha256 = expert_contract_sha256(synchronization)
    if initial_sha256 != manifest.initial_synchronization_sha256:
        raise ExpertContractError("initial_synchronization_sha256")
    state = ExpertStateV1(
        schema_version=1,
        session_id=manifest.session_id,
        expert_manifest_sha256=manifest.manifest_sha256,
        match_binding_universe_sha256=universe_sha256,
        sync_policy_sha256=policy_sha256,
        initial_synchronization_sha256=initial_sha256,
        synchronization=synchronization,
        rejected_parent_count=0,
        halted=False,
        halt_reason=None,
    )
    if (
        len(canonical_expert_bytes(synchronization)) > 131_064
        or len(canonical_expert_bytes(state)) > 131_064
    ):
        raise ExpertContractError("initial_expert_capacity")
    return state
