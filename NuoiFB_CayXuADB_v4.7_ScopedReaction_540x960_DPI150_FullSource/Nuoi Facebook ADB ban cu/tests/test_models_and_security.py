from __future__ import annotations

import unittest
from unittest.mock import patch

from automation.jobs.account_binding import AccountBindingError, AccountBindingRegistry
from automation.jobs.config import JobAutomationConfig
from automation.jobs.executors.reaction import api_action_to_internal
from automation.jobs.models import (
    DeviceAccountBinding,
    JobExecutionContext,
    JobState,
    TerminalApiAction,
)
from automation.jobs.state_machine import JobStateMachine
from core.config import RuntimeConfig
from integrations.golike_api import JobAction, NormalizedJob, redact_sensitive_data
from integrations.golike_service import GoLikeJobService, sanitize_message


def make_job() -> NormalizedJob:
    return NormalizedJob(
        id="100",
        object_id="200",
        link="https://www.facebook.com/user/posts/200",
        provider_type="care",
        action=JobAction.CARE,
        reaction="care",
        reward=10,
        raw={},
    )


def make_binding(ld_index: int, uid: str) -> DeviceAccountBinding:
    return DeviceAccountBinding(
        ld_index=ld_index,
        adb_serial=f"localhost:{5555 + 2 * ld_index}",
        facebook_uid=uid,
        provider_account_id=str(1000 + ld_index),
        facebook_name=f"Account {ld_index}",
    )


class ModelsAndSecurityTests(unittest.TestCase):
    def test_care_mapping(self) -> None:
        self.assertEqual(api_action_to_internal(JobAction.CARE), "thuongthuong")

    def test_only_one_terminal_api_action(self) -> None:
        context = JobExecutionContext(make_job(), make_binding(1, "123456789"))
        context.set_terminal_api_action(TerminalApiAction.SUBMITTED)
        with self.assertRaises(RuntimeError):
            context.set_terminal_api_action(TerminalApiAction.REPORTED)

    def test_failure_state_transition(self) -> None:
        machine = JobStateMachine()
        waiting = machine.transition_state(
            JobState.NAVIGATING,
            JobState.WAITING_TARGET,
        )
        self.assertEqual(waiting, JobState.WAITING_TARGET)
        current = machine.transition_state(JobState.VERIFYING_ACTION, JobState.REPORTING)
        self.assertEqual(current, JobState.REPORTING)
        with self.assertRaises(RuntimeError):
            machine.transition_state(JobState.REPORTING, JobState.SUBMITTING)

    def test_binding_uid_uniqueness(self) -> None:
        registry = AccountBindingRegistry()
        registry.bind(make_binding(1, "123456789"))
        with self.assertRaises(AccountBindingError):
            registry.bind(make_binding(2, "123456789"))

    def test_sensitive_redaction(self) -> None:
        token = "secret.jwt.value"
        message = sanitize_message(
            f"Authorization: Bearer {token}; g-auth=abcdef; firebase_id=xyz",
            secrets=[token],
        )
        self.assertNotIn(token, message)
        self.assertNotIn("abcdef", message)
        self.assertNotIn("xyz", message)
        redacted = redact_sensitive_data(
            {"Authorization": token, "nested": {"signing_key": "key"}}
        )
        self.assertEqual(redacted["Authorization"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["signing_key"], "[REDACTED]")

    def test_job_config_validation(self) -> None:
        defaults = JobAutomationConfig()
        self.assertEqual(defaults.feed_max_search_swipes, 3)
        self.assertEqual(defaults.screen_width_px, 540)
        self.assertEqual(defaults.screen_height_px, 960)
        self.assertEqual(defaults.screen_dpi, 150)
        self.assertTrue(defaults.low_job_fallback_enabled)
        self.assertEqual(defaults.low_job_fallback_after_empty, 3)
        config = JobAutomationConfig(
            between_jobs_min_sec=3,
            between_jobs_max_sec=2,
        )
        with self.assertRaises(ValueError):
            config.validate()
        with self.assertRaises(ValueError):
            JobAutomationConfig(
                job_route_timeout_sec=2,
                job_route_poll_interval_sec=2,
            ).validate()
        with self.assertRaises(ValueError):
            JobAutomationConfig(low_job_fallback_after_empty=0).validate()
        JobAutomationConfig(low_job_fallback_enabled=False).validate()

    def test_get_job_forwards_exact_low_job_wire_parameter(self) -> None:
        response = {"success": True, "status": 200, "data": []}
        with GoLikeJobService("test-token") as service:
            with patch.object(service.client, "_request", return_value=response) as request:
                service.get_jobs(
                    facebook_uid="123456789",
                    server="sv-test",
                    low_job=0,
                )
                self.assertEqual(
                    request.call_args.kwargs["params"],
                    {
                        "fb_id": "123456789",
                        "server": "sv-test",
                        "low_job": 0,
                    },
                )
                service.get_jobs(
                    facebook_uid="123456789",
                    server="sv-test",
                    low_job=1,
                )
                self.assertEqual(request.call_args.kwargs["params"]["low_job"], 1)

    def test_care_mode_runtime_default_still_launches_facebook(self) -> None:
        self.assertTrue(RuntimeConfig().launch_app)


if __name__ == "__main__":
    unittest.main()
