from __future__ import annotations

from collections.abc import Sequence

from .workflow import SharedStatusWorkflowTopology, WorkflowEntryRoute, WorkflowStage


def build_linear_workflow_topology(
    *,
    rabbitmq_url: str,
    workflow_exchange: str,
    status_exchange: str,
    status_queue: str,
    stage_names: Sequence[str],
    queue_prefix: str = "workflow",
) -> SharedStatusWorkflowTopology:
    stages = tuple(_stage(queue_prefix, stage_name) for stage_name in stage_names)
    return SharedStatusWorkflowTopology(
        rabbitmq_url=rabbitmq_url,
        workflow_exchange=workflow_exchange,
        status_exchange=status_exchange,
        status_queue=status_queue,
        stages=stages,
    )


def build_search_aggregate_workflow_topology(
    *,
    rabbitmq_url: str,
    workflow_exchange: str,
    status_exchange: str,
    status_queue: str,
    planner_stage: str = "planner",
    search_stages: Sequence[str] = ("docsearcher", "websearcher"),
    aggregate_stage: str = "researcher_aggregator",
    writer_stage: str = "writer",
    queue_prefix: str = "workflow",
) -> SharedStatusWorkflowTopology:
    ordered = [planner_stage, *search_stages, aggregate_stage, writer_stage]
    stages = tuple(_stage(queue_prefix, stage_name) for stage_name in ordered)
    return SharedStatusWorkflowTopology(
        rabbitmq_url=rabbitmq_url,
        workflow_exchange=workflow_exchange,
        status_exchange=status_exchange,
        status_queue=status_queue,
        stages=stages,
        entry_routes=(
            WorkflowEntryRoute(
                name="planner",
                routing_key=f"{planner_stage}.in",
                target_stage=planner_stage,
            ),
        ),
    )


def _stage(queue_prefix: str, stage_name: str) -> WorkflowStage:
    return WorkflowStage(
        name=stage_name,
        queue=f"{queue_prefix}.{stage_name}",
        binding_keys=(f"{stage_name}.in",),
        publish_routing_key=f"{stage_name}.in",
    )


__all__ = ["build_linear_workflow_topology", "build_search_aggregate_workflow_topology"]
