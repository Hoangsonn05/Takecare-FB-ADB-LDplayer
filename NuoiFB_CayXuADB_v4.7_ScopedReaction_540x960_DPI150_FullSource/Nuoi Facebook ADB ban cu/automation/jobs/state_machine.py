from __future__ import annotations

from .models import JobExecutionContext, JobState

_ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.IDLE: frozenset({JobState.AUTHENTICATING, JobState.STOPPING}),
    JobState.AUTHENTICATING: frozenset(
        {JobState.AUTHENTICATED, JobState.ERROR, JobState.STOPPING}
    ),
    JobState.AUTHENTICATED: frozenset(
        {JobState.DEVICE_BOOTSTRAP, JobState.STOPPING}
    ),
    JobState.DEVICE_BOOTSTRAP: frozenset(
        {JobState.GETTING_JOB, JobState.ERROR, JobState.STOPPING}
    ),
    JobState.GETTING_JOB: frozenset(
        {
            JobState.VALIDATING_JOB,
            JobState.COOLDOWN,
            JobState.RECOVERING,
            JobState.ERROR,
            JobState.STOPPING,
        }
    ),
    JobState.VALIDATING_JOB: frozenset(
        {JobState.NAVIGATING, JobState.REPORTING, JobState.COOLDOWN, JobState.STOPPING}
    ),
    JobState.NAVIGATING: frozenset(
        {
            JobState.WAITING_TARGET,
            JobState.VERIFYING_TARGET,
            JobState.REPORTING,
            JobState.COOLDOWN,
            JobState.STOPPING,
        }
    ),
    JobState.WAITING_TARGET: frozenset(
        {
            JobState.VERIFYING_TARGET,
            JobState.NAVIGATING,
            JobState.REPORTING,
            JobState.COOLDOWN,
            JobState.STOPPING,
        }
    ),
    JobState.VERIFYING_TARGET: frozenset(
        {
            JobState.CLASSIFYING_SCREEN,
            JobState.NAVIGATING,
            JobState.REPORTING,
            JobState.COOLDOWN,
            JobState.STOPPING,
        }
    ),
    JobState.CLASSIFYING_SCREEN: frozenset(
        {JobState.LOCATING_ACTION, JobState.REPORTING, JobState.STOPPING}
    ),
    JobState.LOCATING_ACTION: frozenset(
        {JobState.EXECUTING_ACTION, JobState.REPORTING, JobState.STOPPING}
    ),
    JobState.EXECUTING_ACTION: frozenset(
        {JobState.VERIFYING_ACTION, JobState.REPORTING, JobState.STOPPING}
    ),
    JobState.VERIFYING_ACTION: frozenset(
        {JobState.SUBMITTING, JobState.REPORTING, JobState.STOPPING}
    ),
    JobState.SUBMITTING: frozenset(
        {JobState.COOLDOWN, JobState.ERROR, JobState.STOPPING}
    ),
    JobState.REPORTING: frozenset(
        {JobState.COOLDOWN, JobState.ERROR, JobState.STOPPING}
    ),
    JobState.COOLDOWN: frozenset({JobState.GETTING_JOB, JobState.STOPPING}),
    JobState.RECOVERING: frozenset(
        {JobState.GETTING_JOB, JobState.ERROR, JobState.STOPPING}
    ),
    JobState.ERROR: frozenset({JobState.STOPPING}),
    JobState.STOPPING: frozenset({JobState.STOPPED}),
    JobState.STOPPED: frozenset(),
}


class JobStateMachine:
    def transition_state(self, current: JobState, next_state: JobState) -> JobState:
        if next_state is current:
            return current
        allowed = _ALLOWED.get(current, frozenset())
        if next_state not in allowed:
            raise RuntimeError(
                f"invalid job state transition: {current.value} -> {next_state.value}"
            )
        return next_state

    def transition(self, context: JobExecutionContext, next_state: JobState) -> None:
        context.state = self.transition_state(context.state, next_state)
