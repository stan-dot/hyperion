import uuid
from typing import Callable
from unittest.mock import MagicMock, patch

import bluesky.preprocessors as bpp
import pytest
from bluesky.run_engine import RunEngine

from artemis.exceptions import WarningException
from artemis.experiment_plans.fast_grid_scan_plan import (
    FGSComposite,
    get_plan,
    read_hardware_for_ispyb,
    run_gridscan,
)
from artemis.external_interaction.callbacks.fgs.fgs_callback_collection import (
    FGSCallbackCollection,
)
from artemis.external_interaction.system_tests.conftest import (  # noqa
    fetch_comment,
    zocalo_env,
)
from artemis.external_interaction.system_tests.test_ispyb_dev_connection import (
    ISPYB_CONFIG,
)
from artemis.parameters.plan_specific.fgs_internal_params import FGSInternalParameters


@pytest.mark.s03
@patch("bluesky.plan_stubs.wait", autospec=True)
@patch("bluesky.plan_stubs.kickoff", autospec=True)
@patch("bluesky.plan_stubs.complete", autospec=True)
@patch("artemis.experiment_plans.fast_grid_scan_plan.wait_for_fgs_valid", autospec=True)
def test_run_gridscan(
    wait_for_fgs_valid: MagicMock,
    complete: MagicMock,
    kickoff: MagicMock,
    wait: MagicMock,
    params: FGSInternalParameters,
    RE: RunEngine,
    fgs_composite: FGSComposite,
):
    fgs_composite.eiger.stage = lambda: True
    fgs_composite.eiger.unstage = lambda: True
    fgs_composite.eiger.set_detector_parameters(params.artemis_params.detector_params)
    # Would be better to use get_plan instead but eiger doesn't work well in S03
    RE(run_gridscan(fgs_composite, params))


@pytest.mark.s03
def test_read_hardware_for_ispyb(
    RE: RunEngine,
    fgs_composite: FGSComposite,
):
    undulator = fgs_composite.undulator
    synchrotron = fgs_composite.synchrotron
    slit_gaps = fgs_composite.s4_slit_gaps
    attenuator = fgs_composite.attenuator
    flux = fgs_composite.flux

    @bpp.run_decorator()
    def read_run(u, s, g, a, f):
        yield from read_hardware_for_ispyb(u, s, g, a, f)

    RE(read_run(undulator, synchrotron, slit_gaps, attenuator, flux))


@pytest.mark.s03
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.fast_grid_scan_composite",
    autospec=True,
)
@patch("bluesky.plan_stubs.wait", autospec=True)
@patch("bluesky.plan_stubs.kickoff", autospec=True)
@patch("bluesky.plan_stubs.complete", autospec=True)
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.run_gridscan_and_move", autospec=True
)
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.set_zebra_shutter_to_manual",
    autospec=True,
)
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.FGSCallbackCollection",
    autospec=True,
)
def test_full_plan_tidies_at_end(
    callbacks: MagicMock,
    set_shutter_to_manual: MagicMock,
    run_gridscan_and_move: MagicMock,
    complete: MagicMock,
    kickoff: MagicMock,
    wait: MagicMock,
    fgs_composite: FGSComposite,
    params: FGSInternalParameters,
    RE: RunEngine,
):
    RE(get_plan(params))
    set_shutter_to_manual.assert_called_once()


@pytest.mark.s03
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.fast_grid_scan_composite",
    autospec=True,
)
@patch("bluesky.plan_stubs.wait", autospec=True)
@patch("bluesky.plan_stubs.kickoff", autospec=True)
@patch("bluesky.plan_stubs.complete", autospec=True)
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.run_gridscan_and_move", autospec=True
)
@patch(
    "artemis.experiment_plans.fast_grid_scan_plan.set_zebra_shutter_to_manual",
    autospec=True,
)
def test_full_plan_tidies_at_end_when_plan_fails(
    set_shutter_to_manual: MagicMock,
    run_gridscan_and_move: MagicMock,
    complete: MagicMock,
    kickoff: MagicMock,
    wait: MagicMock,
    fgs_composite: FGSComposite,
    params: FGSInternalParameters,
    RE: RunEngine,
):
    run_gridscan_and_move.side_effect = Exception()
    with pytest.raises(Exception):
        RE(get_plan(params))
    set_shutter_to_manual.assert_called_once()


@pytest.mark.s03
def test_GIVEN_scan_invalid_WHEN_plan_run_THEN_ispyb_entry_made_but_no_zocalo_entry(
    RE: RunEngine,
    fgs_composite: FGSComposite,
    fetch_comment: Callable,
    params: FGSInternalParameters,
):
    params.artemis_params.detector_params.directory = "./tmp"
    params.artemis_params.detector_params.prefix = str(uuid.uuid1())
    params.artemis_params.ispyb_params.visit_path = "/dls/i03/data/2022/cm31105-5/"

    # Currently s03 calls anything with z_steps > 1 invalid
    params.experiment_params.z_steps = 100

    mock_start_zocalo = MagicMock()

    callbacks = FGSCallbackCollection.from_params(params)
    callbacks.ispyb_handler.ispyb.ISPYB_CONFIG_PATH = ISPYB_CONFIG
    callbacks.zocalo_handler.zocalo_interactor.run_start = mock_start_zocalo

    with patch(
        "artemis.experiment_plans.fast_grid_scan_plan.FGSCallbackCollection.from_params",
        return_value=callbacks,
        autospec=True,
    ):
        with pytest.raises(WarningException):
            RE(get_plan(params))

    dcid_used = callbacks.ispyb_handler.ispyb.datacollection_ids[0]

    comment = fetch_comment(dcid_used)

    assert "too long/short/bent" in comment
    mock_start_zocalo.assert_not_called()


@pytest.mark.s03
@patch("artemis.experiment_plans.fast_grid_scan_plan.bps.kickoff", autospec=True)
@patch("artemis.experiment_plans.fast_grid_scan_plan.bps.complete", autospec=True)
def test_WHEN_plan_run_THEN_move_to_centre_returned_from_zocalo_expected_centre(
    complete: MagicMock,
    kickoff: MagicMock,
    RE: RunEngine,
    fgs_composite: FGSComposite,
    zocalo_env: None,
    params: FGSInternalParameters,
):
    """This test currently avoids hardware interaction and is mostly confirming
    interaction with dev_ispyb and dev_zocalo"""

    params.artemis_params.detector_params.directory = "./tmp"
    params.artemis_params.detector_params.prefix = str(uuid.uuid1())
    params.artemis_params.ispyb_params.visit_path = "/dls/i03/data/2022/cm31105-5/"

    # Currently s03 calls anything with z_steps > 1 invalid
    params.experiment_params.z_steps = 1

    fgs_composite.eiger.stage = lambda: True
    fgs_composite.eiger.unstage = lambda: True

    callbacks = FGSCallbackCollection.from_params(params)
    callbacks.ispyb_handler.ispyb.ISPYB_CONFIG_PATH = ISPYB_CONFIG

    with patch(
        "artemis.experiment_plans.fast_grid_scan_plan.FGSCallbackCollection.from_params",
        return_value=callbacks,
        autospec=True,
    ), patch(
        "artemis.experiment_plans.fast_grid_scan_plan.fast_grid_scan_composite",
        fgs_composite,
    ):
        RE(get_plan(params))

    # The following numbers are derived from the centre returned in fake_zocalo
    assert fgs_composite.sample_motors.x.user_readback.get() == pytest.approx(0.05)
    assert fgs_composite.sample_motors.y.user_readback.get() == pytest.approx(0.15)
    assert fgs_composite.sample_motors.z.user_readback.get() == pytest.approx(0.25)
