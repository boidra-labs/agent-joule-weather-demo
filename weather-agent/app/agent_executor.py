"""A2A executor bridge — wires WeatherAgent into the a2a-sdk 1.x server runtime."""

from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Part, Task, TaskState
from opentelemetry.propagate import extract

from app.agent import WeatherAgent
from app.bootstrap import configure_memory

logger = logging.getLogger(__name__)


def _text_part(text: str) -> Part:
    return Part(text=text)


class WeatherAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.agent = WeatherAgent(memory_client=configure_memory())

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        query = context.get_user_input()
        task_id = context.task_id
        context_id = context.context_id

        # Extract W3C traceparent from caller (Joule / orchestrator) to link traces
        carrier = dict(getattr(context, "headers", {}) or {})
        parent_ctx = extract(carrier)

        # Enqueue the Task object first — required before any status events
        task = Task(id=task_id, context_id=context_id)
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()

        try:
            async for item in self.agent.stream(query, context_id, parent_context=parent_ctx):
                if not item["is_task_complete"] and not item["require_user_input"]:
                    msg = updater.new_agent_message([_text_part(item["content"])])
                    await updater.update_status(TaskState.TASK_STATE_WORKING, message=msg)
                elif item["require_user_input"]:
                    msg = updater.new_agent_message([_text_part(item["content"])])
                    await updater.requires_input(message=msg)
                    return
                else:
                    await updater.add_artifact(
                        [_text_part(item["content"])],
                        name="agent_result",
                    )
                    await updater.complete()
                    return
        except Exception:
            logger.exception("Agent execution error")
            await updater.failed()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task_id = context.task_id
        context_id = context.context_id
        task = Task(id=task_id, context_id=context_id)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel()
