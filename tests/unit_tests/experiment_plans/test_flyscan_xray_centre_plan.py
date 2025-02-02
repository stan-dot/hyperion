import types
from unittest.mock import MagicMock, call, patch

import bluesky.preprocessors as bpp
import numpy as np
import pytest
from bluesky.run_engine import RunEngine
from dodal.devices.det_dim_constants import (
    EIGER2_X_4M_DIMENSION,
    EIGER_TYPE_EIGER2_X_4M,
    EIGER_TYPE_EIGER2_X_16M,
)
from dodal.devices.fast_grid_scan import FastGridScan
from ophyd.sim import make_fake_device
from ophyd.status import Status

from hyperion.device_setup_plans.read_hardware_for_setup import (
    read_hardware_for_ispyb_during_collection,
    read_hardware_for_ispyb_pre_collection,
)
from hyperion.exceptions import WarningException
from hyperion.experiment_plans.flyscan_xray_centre_plan import (
    FlyScanXRayCentreComposite,
    flyscan_xray_centre,
    read_hardware_for_ispyb_pre_collection,
    run_gridscan,
    run_gridscan_and_move,
    wait_for_gridscan_valid,
)
from hyperion.external_interaction.callbacks.logging_callback import (
    VerbosePlanExecutionLoggingCallback,
)
from hyperion.external_interaction.callbacks.xray_centre.callback_collection import (
    XrayCentreCallbackCollection,
)
from hyperion.external_interaction.callbacks.xray_centre.ispyb_callback import (
    GridscanISPyBCallback,
)
from hyperion.external_interaction.ispyb.store_in_ispyb import (
    IspybIds,
    Store3DGridscanInIspyb,
)
from hyperion.log import set_up_logging_handlers
from hyperion.parameters import external_parameters
from hyperion.parameters.constants import (
    GRIDSCAN_OUTER_PLAN,
    ISPYB_HARDWARE_READ_PLAN,
    ISPYB_TRANSMISSION_FLUX_READ_PLAN,
)
from hyperion.parameters.plan_specific.gridscan_internal_params import (
    GridscanInternalParameters,
)

from ...system_tests.external_interaction.conftest import (
    TEST_RESULT_LARGE,
    TEST_RESULT_MEDIUM,
    TEST_RESULT_SMALL,
)
from ..external_interaction.callbacks.xray_centre.conftest import TestData
from .conftest import (
    modified_interactor_mock,
    modified_store_grid_scan_mock,
)


@pytest.fixture
def ispyb_plan(test_fgs_params):
    @bpp.set_run_key_decorator(GRIDSCAN_OUTER_PLAN)
    @bpp.run_decorator(  # attach experiment metadata to the start document
        md={
            "subplan_name": GRIDSCAN_OUTER_PLAN,
            "hyperion_internal_parameters": test_fgs_params.json(),
        }
    )
    def standalone_read_hardware_for_ispyb(und, syn, slits, attn, fl):
        yield from read_hardware_for_ispyb_pre_collection(und, syn, slits)
        yield from read_hardware_for_ispyb_during_collection(attn, fl)

    return standalone_read_hardware_for_ispyb


@patch(
    "hyperion.external_interaction.callbacks.xray_centre.ispyb_callback.Store3DGridscanInIspyb",
    modified_store_grid_scan_mock,
)
class TestFlyscanXrayCentrePlan:
    def test_given_full_parameters_dict_when_detector_name_used_and_converted_then_detector_constants_correct(
        self,
        test_fgs_params: GridscanInternalParameters,
    ):
        assert (
            test_fgs_params.hyperion_params.detector_params.detector_size_constants.det_type_string
            == EIGER_TYPE_EIGER2_X_16M
        )
        raw_params_dict = external_parameters.from_file()
        raw_params_dict["hyperion_params"]["detector_params"][
            "detector_size_constants"
        ] = EIGER_TYPE_EIGER2_X_4M
        params: GridscanInternalParameters = GridscanInternalParameters(
            **raw_params_dict
        )
        det_dimension = (
            params.hyperion_params.detector_params.detector_size_constants.det_dimension
        )
        assert det_dimension == EIGER2_X_4M_DIMENSION

    def test_when_run_gridscan_called_then_generator_returned(
        self,
    ):
        plan = run_gridscan(MagicMock(), MagicMock())
        assert isinstance(plan, types.GeneratorType)

    def test_read_hardware_for_ispyb_updates_from_ophyd_devices(
        self,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
        ispyb_plan,
    ):
        undulator_test_value = 1.234

        fake_fgs_composite.undulator.current_gap.sim_put(undulator_test_value)  # type: ignore

        synchrotron_test_value = "test"
        fake_fgs_composite.synchrotron.machine_status.synchrotron_mode.sim_put(  # type: ignore
            synchrotron_test_value
        )

        transmission_test_value = 0.01
        fake_fgs_composite.attenuator.actual_transmission.sim_put(transmission_test_value)  # type: ignore

        xgap_test_value = 0.1234
        ygap_test_value = 0.2345
        fake_fgs_composite.s4_slit_gaps.xgap.user_readback.sim_put(xgap_test_value)  # type: ignore
        fake_fgs_composite.s4_slit_gaps.ygap.user_readback.sim_put(ygap_test_value)  # type: ignore
        flux_test_value = 10.0
        fake_fgs_composite.flux.flux_reading.sim_put(flux_test_value)  # type: ignore

        test_ispyb_callback = GridscanISPyBCallback()
        test_ispyb_callback.active = True
        test_ispyb_callback.ispyb = MagicMock(spec=Store3DGridscanInIspyb)
        test_ispyb_callback.ispyb.begin_deposition.return_value = IspybIds(
            data_collection_ids=(2, 3), data_collection_group_id=5, grid_ids=(7, 8, 9)
        )
        RE.subscribe(test_ispyb_callback)

        RE(
            ispyb_plan(
                fake_fgs_composite.undulator,
                fake_fgs_composite.synchrotron,
                fake_fgs_composite.s4_slit_gaps,
                fake_fgs_composite.attenuator,
                fake_fgs_composite.flux,
            )
        )
        params = test_ispyb_callback.params

        assert params.hyperion_params.ispyb_params.undulator_gap == undulator_test_value  # type: ignore
        assert (
            params.hyperion_params.ispyb_params.synchrotron_mode  # type: ignore
            == synchrotron_test_value
        )
        assert params.hyperion_params.ispyb_params.slit_gap_size_x == xgap_test_value  # type: ignore
        assert params.hyperion_params.ispyb_params.slit_gap_size_y == ygap_test_value  # type: ignore
        assert (
            params.hyperion_params.ispyb_params.transmission_fraction  # type: ignore
            == transmission_test_value
        )
        assert params.hyperion_params.ispyb_params.flux == flux_test_value  # type: ignore

    @patch(
        "dodal.devices.aperturescatterguard.ApertureScatterguard._safe_move_within_datacollection_range"
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.run_gridscan", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.move_x_y_z", autospec=True
    )
    def test_results_adjusted_and_passed_to_move_xyz(
        self,
        move_x_y_z: MagicMock,
        run_gridscan: MagicMock,
        move_aperture: MagicMock,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        mock_subscriptions: XrayCentreCallbackCollection,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        set_up_logging_handlers(logging_level="INFO", dev_mode=True)
        RE.subscribe(VerbosePlanExecutionLoggingCallback())

        mock_subscriptions.ispyb_handler.activity_gated_start(
            {
                "subplan_name": GRIDSCAN_OUTER_PLAN,
                "hyperion_internal_parameters": test_fgs_params.json(),
            }
        )
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "123abc", "name": ISPYB_HARDWARE_READ_PLAN}
        )
        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "123abc",
                "data": {
                    "undulator_current_gap": 0,
                    "synchrotron_machine_status_synchrotron_mode": 0,
                    "s4_slit_gaps_xgap": 0,
                    "s4_slit_gaps_ygap": 0,
                },
            }
        )
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "abc123", "name": ISPYB_TRANSMISSION_FLUX_READ_PLAN}
        )
        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "abc123",
                "data": {
                    "attenuator_actual_transmission": 0,
                    "flux_flux_reading": 10,
                },
            }
        )

        mock_subscriptions.zocalo_handler.zocalo_interactor.wait_for_result.return_value = (
            TEST_RESULT_LARGE
        )
        RE(
            run_gridscan_and_move(
                fake_fgs_composite,
                test_fgs_params,
                mock_subscriptions,
            )
        )
        mock_subscriptions.zocalo_handler.zocalo_interactor.wait_for_result.return_value = (
            TEST_RESULT_MEDIUM
        )
        RE(
            run_gridscan_and_move(
                fake_fgs_composite,
                test_fgs_params,
                mock_subscriptions,
            )
        )
        mock_subscriptions.zocalo_handler.zocalo_interactor.wait_for_result.return_value = (
            TEST_RESULT_SMALL
        )
        RE(
            run_gridscan_and_move(
                fake_fgs_composite,
                test_fgs_params,
                mock_subscriptions,
            )
        )
        assert fake_fgs_composite.aperture_scatterguard.aperture_positions is not None
        ap_call_large = call(
            *(fake_fgs_composite.aperture_scatterguard.aperture_positions.LARGE)
        )
        ap_call_medium = call(
            *(fake_fgs_composite.aperture_scatterguard.aperture_positions.MEDIUM)
        )

        move_aperture.assert_has_calls(
            [ap_call_large, ap_call_large, ap_call_medium], any_order=True
        )

        mv_call_large = call(
            fake_fgs_composite.sample_motors, 0.05, pytest.approx(0.15), 0.25, wait=True
        )
        mv_call_medium = call(
            fake_fgs_composite.sample_motors, 0.05, pytest.approx(0.15), 0.25, wait=True
        )
        move_x_y_z.assert_has_calls(
            [mv_call_large, mv_call_large, mv_call_medium], any_order=True
        )

    @patch("bluesky.plan_stubs.abs_set", autospec=True)
    def test_results_passed_to_move_motors(
        self,
        bps_abs_set: MagicMock,
        test_fgs_params: GridscanInternalParameters,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        RE: RunEngine,
    ):
        from hyperion.device_setup_plans.manipulate_sample import move_x_y_z

        set_up_logging_handlers(logging_level="INFO", dev_mode=True)
        RE.subscribe(VerbosePlanExecutionLoggingCallback())
        motor_position = (
            test_fgs_params.experiment_params.grid_position_to_motor_position(
                np.array([1, 2, 3])
            )
        )
        RE(move_x_y_z(fake_fgs_composite.sample_motors, *motor_position))
        bps_abs_set.assert_has_calls(
            [
                call(
                    fake_fgs_composite.sample_motors.x,
                    motor_position[0],
                    group="move_x_y_z",
                ),
                call(
                    fake_fgs_composite.sample_motors.y,
                    motor_position[1],
                    group="move_x_y_z",
                ),
                call(
                    fake_fgs_composite.sample_motors.z,
                    motor_position[2],
                    group="move_x_y_z",
                ),
            ],
            any_order=True,
        )

    @patch(
        "dodal.devices.aperturescatterguard.ApertureScatterguard._safe_move_within_datacollection_range",
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.run_gridscan", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.move_x_y_z", autospec=True
    )
    @patch("bluesky.plan_stubs.rd")
    @patch(
        "hyperion.external_interaction.callbacks.xray_centre.zocalo_callback.ZocaloInteractor",
        modified_interactor_mock,
    )
    def test_individual_plans_triggered_once_and_only_once_in_composite_run(
        self,
        rd: MagicMock,
        move_xyz: MagicMock,
        run_gridscan: MagicMock,
        move_aperture: MagicMock,
        mock_subscriptions: XrayCentreCallbackCollection,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        td = TestData()
        mock_subscriptions.ispyb_handler.activity_gated_start(td.test_start_document)
        mock_subscriptions.zocalo_handler.activity_gated_start(td.test_start_document)
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "123abc", "name": ISPYB_HARDWARE_READ_PLAN}
        )

        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "123abc",
                "data": {
                    "undulator_current_gap": 0,
                    "synchrotron_machine_status_synchrotron_mode": 0,
                    "s4_slit_gaps_xgap": 0,
                    "s4_slit_gaps_ygap": 0,
                },
            }
        )
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "abc123", "name": ISPYB_TRANSMISSION_FLUX_READ_PLAN}
        )
        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "abc123",
                "data": {
                    "attenuator_actual_transmission": 0,
                    "flux_flux_reading": 10,
                },
            }
        )

    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.run_gridscan", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.move_x_y_z", autospec=True
    )
    def test_when_gridscan_finished_then_smargon_stub_offsets_are_set(
        self,
        move_xyz: MagicMock,
        run_gridscan: MagicMock,
        mock_subscriptions: XrayCentreCallbackCollection,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "123abc", "name": ISPYB_HARDWARE_READ_PLAN}
        )

        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "123abc",
                "data": {
                    "undulator_current_gap": 0,
                    "synchrotron_machine_status_synchrotron_mode": 0,
                    "s4_slit_gaps_xgap": 0,
                    "s4_slit_gaps_ygap": 0,
                },
            }
        )
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "abc123", "name": ISPYB_TRANSMISSION_FLUX_READ_PLAN}
        )
        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "abc123",
                "data": {
                    "attenuator_actual_transmission": 0,
                    "flux_flux_reading": 10,
                },
            }
        )

        set_up_logging_handlers(logging_level="INFO", dev_mode=True)
        RE.subscribe(VerbosePlanExecutionLoggingCallback())
        mock_subscriptions.zocalo_handler.wait_for_results = MagicMock(
            return_value=(
                (0, 0, 0),
                None,
            )
        )

        RE(
            run_gridscan_and_move(
                fake_fgs_composite,
                test_fgs_params,
                mock_subscriptions,
            )
        )
        assert (
            fake_fgs_composite.smargon.stub_offsets.center_at_current_position.proc.get()
            == 1
        )

    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.run_gridscan", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.move_x_y_z", autospec=True
    )
    def test_given_gridscan_fails_to_centre_then_stub_offsets_not_set(
        self,
        move_xyz: MagicMock,
        run_gridscan: MagicMock,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        class MoveException(Exception):
            pass

        move_xyz.side_effect = MoveException()
        mock_subscriptions = MagicMock()
        mock_subscriptions.zocalo_handler.wait_for_results.return_value = (
            (0, 0, 0),
            None,
        )

        with pytest.raises(MoveException):
            RE(
                run_gridscan_and_move(
                    fake_fgs_composite,
                    test_fgs_params,
                    mock_subscriptions,
                )
            )
        assert (
            fake_fgs_composite.smargon.stub_offsets.center_at_current_position.proc.get()
            == 0
        )

    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.run_gridscan", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.move_x_y_z", autospec=True
    )
    def test_given_setting_stub_offsets_disabled_then_stub_offsets_not_set(
        self,
        move_xyz: MagicMock,
        run_gridscan: MagicMock,
        mock_subscriptions: XrayCentreCallbackCollection,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        test_fgs_params.experiment_params.set_stub_offsets = False
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "123abc", "name": ISPYB_HARDWARE_READ_PLAN}
        )

        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "123abc",
                "data": {
                    "undulator_current_gap": 0,
                    "synchrotron_machine_status_synchrotron_mode": 0,
                    "s4_slit_gaps_xgap": 0,
                    "s4_slit_gaps_ygap": 0,
                },
            }
        )
        mock_subscriptions.ispyb_handler.activity_gated_descriptor(
            {"uid": "abc123", "name": ISPYB_TRANSMISSION_FLUX_READ_PLAN}
        )
        mock_subscriptions.ispyb_handler.activity_gated_event(
            {
                "descriptor": "abc123",
                "data": {
                    "attenuator_actual_transmission": 0,
                    "flux_flux_reading": 10,
                },
            }
        )

        set_up_logging_handlers(logging_level="INFO", dev_mode=True)
        RE.subscribe(VerbosePlanExecutionLoggingCallback())
        mock_subscriptions.zocalo_handler.wait_for_results = MagicMock(
            return_value=(
                (0, 0, 0),
                None,
            )
        )

        RE(
            run_gridscan_and_move(
                fake_fgs_composite,
                test_fgs_params,
                mock_subscriptions,
            )
        )
        assert (
            fake_fgs_composite.smargon.stub_offsets.center_at_current_position.proc.get()
            == 0
        )

    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.sleep", autospec=True
    )
    def test_GIVEN_scan_already_valid_THEN_wait_for_GRIDSCAN_returns_immediately(
        self, patch_sleep: MagicMock, RE: RunEngine
    ):
        test_fgs: FastGridScan = make_fake_device(FastGridScan)(
            "prefix", name="fake_fgs"
        )

        test_fgs.scan_invalid.sim_put(False)  # type: ignore
        test_fgs.position_counter.sim_put(0)  # type: ignore

        RE(wait_for_gridscan_valid(test_fgs))

        patch_sleep.assert_not_called()

    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.sleep", autospec=True
    )
    def test_GIVEN_scan_not_valid_THEN_wait_for_GRIDSCAN_raises_and_sleeps_called(
        self, patch_sleep: MagicMock, RE: RunEngine
    ):
        test_fgs: FastGridScan = make_fake_device(FastGridScan)(
            "prefix", name="fake_fgs"
        )

        test_fgs.scan_invalid.sim_put(True)  # type: ignore
        test_fgs.position_counter.sim_put(0)  # type: ignore
        with pytest.raises(WarningException):
            RE(wait_for_gridscan_valid(test_fgs))

        patch_sleep.assert_called()

    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.abs_set", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.kickoff", autospec=True
    )
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.complete", autospec=True
    )
    @patch("hyperion.experiment_plans.flyscan_xray_centre_plan.bps.mv", autospec=True)
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.wait_for_gridscan_valid",
        autospec=True,
    )
    @patch(
        "hyperion.external_interaction.nexus.write_nexus.NexusWriter",
        autospec=True,
        spec_set=True,
    )
    def test_when_grid_scan_ran_then_eiger_disarmed_before_zocalo_end(
        self,
        nexuswriter,
        wait_for_valid,
        mock_mv,
        mock_complete,
        mock_kickoff,
        mock_abs_set,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        mock_subscriptions: XrayCentreCallbackCollection,
        RE: RunEngine,
    ):
        # Put both mocks in a parent to easily capture order
        mock_parent = MagicMock()
        fake_fgs_composite.eiger.disarm_detector = mock_parent.disarm

        fake_fgs_composite.eiger.filewriters_finished = Status(done=True, success=True)  # type: ignore
        fake_fgs_composite.eiger.odin.check_odin_state = MagicMock(return_value=True)
        fake_fgs_composite.eiger.odin.file_writer.num_captured.sim_put(1200)  # type: ignore
        fake_fgs_composite.eiger.stage = MagicMock(
            return_value=Status(None, None, 0, True, True)
        )
        fake_fgs_composite.xbpm_feedback.pos_stable.sim_put(1)  # type: ignore

        with patch(
            "hyperion.experiment_plans.flyscan_xray_centre_plan.XrayCentreCallbackCollection.setup",
            lambda: mock_subscriptions,
        ), patch(
            "hyperion.external_interaction.callbacks.xray_centre.nexus_callback.NexusWriter.create_nexus_file",
            autospec=True,
        ), patch(
            "hyperion.external_interaction.callbacks.xray_centre.zocalo_callback.ZocaloInteractor",
            lambda _: modified_interactor_mock(mock_parent.run_end),
        ):
            RE(flyscan_xray_centre(fake_fgs_composite, test_fgs_params))

        mock_parent.assert_has_calls([call.disarm(), call.run_end(0), call.run_end(0)])

    @patch("hyperion.experiment_plans.flyscan_xray_centre_plan.bps.wait", autospec=True)
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.complete", autospec=True
    )
    def test_fgs_arms_eiger_without_grid_detect(
        self,
        mock_complete,
        mock_wait,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        fake_fgs_composite.eiger.stage = MagicMock()
        fake_fgs_composite.eiger.unstage = MagicMock()

        RE(run_gridscan(fake_fgs_composite, test_fgs_params))
        fake_fgs_composite.eiger.stage.assert_called_once()
        fake_fgs_composite.eiger.unstage.assert_called_once()

    @patch("hyperion.experiment_plans.flyscan_xray_centre_plan.bps.wait", autospec=True)
    @patch(
        "hyperion.experiment_plans.flyscan_xray_centre_plan.bps.complete", autospec=True
    )
    def test_when_grid_scan_fails_then_detector_disarmed_and_correct_exception_returned(
        self,
        mock_complete,
        mock_wait,
        fake_fgs_composite: FlyScanXRayCentreComposite,
        test_fgs_params: GridscanInternalParameters,
        RE: RunEngine,
    ):
        class CompleteException(Exception):
            pass

        mock_complete.side_effect = CompleteException()

        fake_fgs_composite.eiger.stage = MagicMock(
            return_value=Status(None, None, 0, True, True)
        )

        fake_fgs_composite.eiger.odin.check_odin_state = MagicMock()

        fake_fgs_composite.eiger.disarm_detector = MagicMock()
        fake_fgs_composite.eiger.disable_roi_mode = MagicMock()

        # Without the complete finishing we will not get all the images
        fake_fgs_composite.eiger.ALL_FRAMES_TIMEOUT = 0.1  # type: ignore

        # Want to get the underlying completion error, not the one raised from unstage
        with pytest.raises(CompleteException):
            RE(run_gridscan(fake_fgs_composite, test_fgs_params))

        fake_fgs_composite.eiger.disable_roi_mode.assert_called()
        fake_fgs_composite.eiger.disarm_detector.assert_called()
