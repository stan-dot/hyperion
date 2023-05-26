from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from sys import argv
from time import sleep
from typing import Any, Callable, Mapping, Optional, Type
from unittest.mock import MagicMock, patch

import pytest
from blueapi.core import BlueskyContext, MsgGenerator
from flask.testing import FlaskClient

from artemis.__main__ import Actions, Status, cli_arg_parse, create_app, setup_context
from artemis.external_interaction.callbacks.abstract_plan_callback_collection import (
    AbstractPlanCallbackCollection,
)
from artemis.parameters import external_parameters
from artemis.parameters.internal_parameters.plan_specific.fgs_internal_params import (
    FGSInternalParameters,
)

FGS_ENDPOINT = "/fast_grid_scan/"
START_ENDPOINT = FGS_ENDPOINT + Actions.START.value
STOP_ENDPOINT = Actions.STOP.value
STATUS_ENDPOINT = Actions.STATUS.value
SHUTDOWN_ENDPOINT = Actions.SHUTDOWN.value
TEST_PARAMS = json.dumps(external_parameters.from_file("test_parameters.json"))
TEST_BAD_PARAM_ENDPOINT = "/fgs_real_params/" + Actions.START.value


class MockRunEngine:
    RE_takes_time = True
    aborting_takes_time = False
    error: Optional[str] = None

    def __call__(self, *args: Any, **kwds: Any) -> Any:
        while self.RE_takes_time:
            sleep(0.1)
            if self.error:
                raise Exception(self.error)

    def abort(self):
        while self.aborting_takes_time:
            sleep(0.1)
            if self.error:
                raise Exception(self.error)
        self.RE_takes_time = False

    def subscribe(self, *args):
        pass


@dataclass
class ClientAndRunEngine:
    client: FlaskClient
    mock_run_engine: MockRunEngine


def mock_dict_values(d: dict):
    return {k: MagicMock() for k, _ in d.items()}


TEST_EXPTS = {
    "test_experiment": {
        "setup": MagicMock(),
        "run": MagicMock(),
        "internal_param_type": MagicMock(),
        "experiment_param_type": MagicMock(),
    },
    "test_experiment_no_run": {
        "setup": MagicMock(),
        "internal_param_type": MagicMock(),
        "experiment_param_type": MagicMock(),
    },
    "test_experiment_no_internal_param_type": {
        "setup": MagicMock(),
        "run": MagicMock(),
        "experiment_param_type": MagicMock(),
    },
    "fgs_real_params": {
        "setup": MagicMock(),
        "run": MagicMock(),
        "internal_param_type": FGSInternalParameters,
        "experiment_param_type": MagicMock(),
    },
}


@pytest.fixture
def context_with_test_experiments() -> BlueskyContext:
    context = BlueskyContext()

    @context.plan
    def fast_grid_scan(parameters: Mapping[str, Any]) -> MsgGenerator:
        ...

    return context


@pytest.fixture
def test_env(context_with_test_experiments: BlueskyContext):
    mock_run_engine = MockRunEngine()
    app, worker, context = create_app(
        {"TESTING": True},
        mock_run_engine,
        context=context_with_test_experiments,
    )
    worker.start()

    with app.test_client() as client:
        yield ClientAndRunEngine(client, mock_run_engine)

    worker.stop()


def wait_for_run_engine_status(
    client: FlaskClient,
    status_check: Callable[[str], bool] = lambda status: status != Status.BUSY.value,
    attempts=10,
):
    while attempts != 0:
        response = client.get(STATUS_ENDPOINT)
        response_json = json.loads(response.data)
        if status_check(response_json["status"]):
            return response_json
        else:
            attempts -= 1
            sleep(0.1)
    assert False, "Run engine still busy"


def check_status_in_response(response_object, expected_result: Status):
    response_json = json.loads(response_object.data)
    expected_status = expected_result.value
    actual_status = response_json.get("status")
    status_message = response_json.get("message")
    assert (
        actual_status == expected_status
    ), f"Expected status {expected_status} but was {actual_status}, message was {status_message}"


def test_start_gives_success(test_env: ClientAndRunEngine):
    response = test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    check_status_in_response(response, Status.SUCCESS)


def test_getting_status_return_idle(test_env: ClientAndRunEngine):
    response = test_env.client.get(STATUS_ENDPOINT)
    check_status_in_response(response, Status.IDLE)


def test_getting_status_after_start_sent_returns_busy(
    test_env: ClientAndRunEngine,
):
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    response = test_env.client.get(STATUS_ENDPOINT)
    check_status_in_response(response, Status.BUSY)


def test_putting_bad_plan_fails(test_env: ClientAndRunEngine):
    response = test_env.client.put("/bad_plan/start", data=TEST_PARAMS).json
    assert isinstance(response, dict)
    assert response.get("status") == Status.FAILED.value
    assert (
        response.get("message")
        == "PlanNotFound(\"Experiment plan 'bad_plan' not found in registry.\")"
    )


def test_nonexistant_plan_fails(test_env: ClientAndRunEngine):
    response = test_env.client.put(
        "/test_experiment_nonexistant/start", data=TEST_PARAMS
    ).json
    assert isinstance(response, dict)
    assert response.get("status") == Status.FAILED.value
    assert (
        response.get("message")
        == "PlanNotFound(\"Experiment plan 'test_experiment_nonexistant' not found in registry.\")"
    )


def test_sending_start_twice_fails(test_env: ClientAndRunEngine):
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    response = test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    check_status_in_response(response, Status.FAILED)


def test_given_started_when_stopped_then_success_and_idle_status(
    test_env: ClientAndRunEngine,
):
    test_env.mock_run_engine.aborting_takes_time = True
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    response = test_env.client.put(STOP_ENDPOINT)
    check_status_in_response(response, Status.ABORTING)
    response = test_env.client.get(STATUS_ENDPOINT)
    check_status_in_response(response, Status.ABORTING)
    test_env.mock_run_engine.aborting_takes_time = False
    wait_for_run_engine_status(
        test_env.client, lambda status: status != Status.ABORTING
    )
    check_status_in_response(response, Status.ABORTING)


def test_given_started_when_stopped_and_started_again_then_runs(
    test_env: ClientAndRunEngine,
):
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    test_env.client.put(STOP_ENDPOINT)
    response = test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    check_status_in_response(response, Status.SUCCESS)
    response = test_env.client.get(STATUS_ENDPOINT)
    check_status_in_response(response, Status.BUSY)


def test_given_started_when_RE_stops_on_its_own_with_error_then_error_reported(
    test_env: ClientAndRunEngine,
):
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    error_message = "D'Oh"
    test_env.mock_run_engine.error = error_message
    response_json = wait_for_run_engine_status(test_env.client)
    assert response_json["status"] == Status.FAILED.value
    assert response_json["message"] == 'Exception("D\'Oh")'


def test_when_started_n_returnstatus_interrupted_bc_RE_aborted_thn_error_reptd(
    test_env: ClientAndRunEngine,
):
    test_env.mock_run_engine.aborting_takes_time = True
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    error_message = "D'Oh"
    test_env.client.put(STOP_ENDPOINT)
    test_env.mock_run_engine.error = error_message
    response_json = wait_for_run_engine_status(
        test_env.client, lambda status: status != Status.ABORTING.value
    )
    assert response_json["status"] == Status.FAILED.value
    assert response_json["message"] == 'Exception("D\'Oh")'


def test_given_started_when_RE_stops_on_its_own_happily_then_no_error_reported(
    test_env: ClientAndRunEngine,
):
    test_env.client.put(START_ENDPOINT, data=TEST_PARAMS)
    test_env.mock_run_engine.RE_takes_time = False
    response_json = wait_for_run_engine_status(test_env.client)
    assert response_json["status"] == Status.IDLE.value


def test_start_with_json_file_gives_success(test_env: ClientAndRunEngine):
    with open("test_parameters.json") as test_parameters_file:
        test_parameters_json = test_parameters_file.read()
    response = test_env.client.put(START_ENDPOINT, data=test_parameters_json)
    check_status_in_response(response, Status.SUCCESS)


def test_cli_args_parse():
    argv[1:] = ["--dev", "--logging-level=DEBUG"]
    test_args = cli_arg_parse()
    assert test_args == ("DEBUG", False, True, False)
    argv[1:] = ["--dev", "--logging-level=DEBUG", "--verbose-event-logging"]
    test_args = cli_arg_parse()
    assert test_args == ("DEBUG", True, True, False)
    argv[1:] = [
        "--dev",
        "--logging-level=DEBUG",
        "--verbose-event-logging",
        "--skip-startup-connection",
    ]
    test_args = cli_arg_parse()
    assert test_args == ("DEBUG", True, True, True)


@pytest.mark.skip(reason="fixed in #621")
@patch("dodal.i03.ApertureScatterguard")
@patch("dodal.i03.Backlight")
@patch("dodal.i03.EigerDetector")
@patch("dodal.i03.FastGridScan")
@patch("dodal.i03.S4SlitGaps")
@patch("dodal.i03.Smargon")
@patch("dodal.i03.Synchrotron")
@patch("dodal.i03.Undulator")
@patch("dodal.i03.Zebra")
@patch("artemis.experiment_plans.fast_grid_scan_plan.get_beamline_parameters")
def test_when_context_setup_then_plans_are_setup_and_devices_connected(
    mock_get_beamline_params,
    zebra,
    undulator,
    synchrotron,
    smargon,
    s4_slits,
    fast_grid_scan,
    eiger,
    backlight,
    aperture_scatterguard,
):
    setup_context()
    zebra.return_value.wait_for_connection.assert_called_once()
    undulator.return_value.wait_for_connection.assert_called_once()
    synchrotron.return_value.wait_for_connection.assert_called_once()
    smargon.return_value.wait_for_connection.assert_called_once()
    s4_slits.return_value.wait_for_connection.assert_called_once()
    fast_grid_scan.return_value.wait_for_connection.assert_called_once()
    eiger.return_value.wait_for_connection.assert_not_called()  # can't wait on eiger
    backlight.return_value.wait_for_connection.assert_called_once()
    aperture_scatterguard.return_value.wait_for_connection.assert_called_once()


@patch("artemis.experiment_plans.fast_grid_scan_plan.EigerDetector")
@patch("artemis.experiment_plans.fast_grid_scan_plan.FGSComposite")
@patch("artemis.experiment_plans.fast_grid_scan_plan.get_beamline_parameters")
def test_when_context_setup_and_skip_flag_is_set_then_plans_are_setup_and_devices_are_not_connected(
    mock_get_beamline_params, mock_fgs, mock_eiger
):
    setup_context(skip_startup_connection=True)
    mock_fgs.return_value.wait_for_connection.assert_not_called()


@patch("artemis.experiment_plans.fast_grid_scan_plan.EigerDetector")
@patch("artemis.experiment_plans.fast_grid_scan_plan.FGSComposite")
@patch("artemis.experiment_plans.fast_grid_scan_plan.get_beamline_parameters")
@patch("artemis.experiment_plans.fast_grid_scan_plan.create_devices")
def test_when_context_setup_and_skip_flag_is_set_then_setup_called_upon_start(
    mock_setup, mock_get_beamline_params, mock_fgs, mock_eiger
):
    mock_setup = MagicMock()
    context = setup_context(skip_startup_connection=True)
    mock_setup.assert_not_called()
    runner.start(MagicMock(), MagicMock(), "fast_grid_scan")
    mock_setup.assert_called_once()


@patch("artemis.experiment_plans.fast_grid_scan_plan.EigerDetector")
@patch("artemis.experiment_plans.fast_grid_scan_plan.FGSComposite")
@patch("artemis.experiment_plans.fast_grid_scan_plan.get_beamline_parameters")
def test_when_blueskyrunner_initiated_and_skip_flag_is_not_set_then_all_plans_setup(
    mock_get_beamline_params,
    mock_fgs,
    mock_eiger,
):
    mock_setup = MagicMock()
    with patch.dict(
        "artemis.__main__.PLAN_REGISTRY",
        {
            "fast_grid_scan": {
                "setup": mock_setup,
                "run": MagicMock(),
                "param_type": MagicMock(),
            },
            "other_plan": {
                "setup": mock_setup,
                "run": MagicMock(),
                "param_type": MagicMock(),
            },
            "yet_another_plan": {
                "setup": mock_setup,
                "run": MagicMock(),
                "param_type": MagicMock(),
            },
        },
        clear=True,
    ):
        setup_context(skip_startup_connection=False)
        assert mock_setup.call_count == 3


def test_log_on_invalid_json_params(caplog, test_env: ClientAndRunEngine):
    response = test_env.client.put(TEST_BAD_PARAM_ENDPOINT, data='{"bad":1}').json
    assert isinstance(response, dict)
    assert response.get("status") == Status.FAILED.value
    assert (
        response.get("message")
        == "<ValidationError: \"{'bad': 1} does not have enough properties\">"
    )
    assert "Invalid json parameters" in caplog.text
