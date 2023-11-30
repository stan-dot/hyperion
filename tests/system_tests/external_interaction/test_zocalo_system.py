import bluesky.plan_stubs as bps
import numpy as np
import pytest
import pytest_asyncio
from bluesky.run_engine import RunEngine
from dodal.devices.zocalo import ZocaloResults

from hyperion.external_interaction.callbacks.xray_centre.callback_collection import (
    XrayCentreCallbackCollection,
)
from hyperion.external_interaction.ispyb.store_in_ispyb import IspybIds
from hyperion.parameters.constants import GRIDSCAN_OUTER_PLAN
from hyperion.parameters.plan_specific.gridscan_internal_params import (
    GridscanInternalParameters,
)

from .conftest import (
    TEST_RESULT_LARGE,
    TEST_RESULT_SMALL,
)


@pytest_asyncio.fixture
async def zocalo_device():
    zd = ZocaloResults()
    zd.timeout_s = 5
    await zd.connect()
    return zd


def trigger_zocalo_results_plan(zocalo):
    yield from bps.open_run()
    yield from bps.kickoff(zocalo, wait=True)
    yield from bps.complete(zocalo, wait=True)
    yield from bps.close_run()


@pytest.mark.s03
@pytest.mark.asyncio
async def test_when_running_start_stop_then_get_expected_returned_results(
    dummy_params, zocalo_env, zocalo_device: ZocaloResults, RE: RunEngine
):
    start_doc = {
        "subplan_name": GRIDSCAN_OUTER_PLAN,
        "hyperion_internal_parameters": dummy_params.json(),
    }
    zc = XrayCentreCallbackCollection.setup().zocalo_handler
    zc.activity_gated_start(start_doc)
    dcids = (1, 2)
    zc.ispyb.ispyb_ids = IspybIds(
        data_collection_ids=dcids, data_collection_group_id=4, grid_ids=(0,)
    )
    for dcid in dcids:
        zc.zocalo_interactor.run_start(dcid)
    for dcid in dcids:
        zc.zocalo_interactor.run_end(dcid)
    RE(trigger_zocalo_results_plan(zocalo_device))
    result = await zocalo_device.read()
    assert result["zocalo_results-results"]["value"][0] == TEST_RESULT_LARGE[0]


@pytest.fixture
def run_zocalo_with_dev_ispyb(dummy_params: GridscanInternalParameters, dummy_ispyb_3d):
    def inner(sample_name="", fallback=np.array([0, 0, 0])):
        dummy_params.hyperion_params.detector_params.prefix = sample_name
        start_doc = {
            "subplan_name": GRIDSCAN_OUTER_PLAN,
            "hyperion_internal_parameters": dummy_params.json(),
        }
        cbs = XrayCentreCallbackCollection.setup()
        zc = cbs.zocalo_handler
        ispyb = cbs.ispyb_handler
        ispyb.activity_gated_start(start_doc)
        zc.activity_gated_start(start_doc)
        zc.ispyb.ispyb.ISPYB_CONFIG_PATH = dummy_ispyb_3d.ISPYB_CONFIG_PATH
        zc.ispyb.ispyb_ids = zc.ispyb.ispyb.begin_deposition()
        assert isinstance(zc.ispyb.ispyb_ids.data_collection_ids, tuple)
        for dcid in zc.ispyb.ispyb_ids.data_collection_ids:
            zc.zocalo_interactor.run_start(dcid)
        zc.activity_gated_stop({})
        centre, bbox = zc.wait_for_results(fallback_xyz=fallback)
        return zc, centre

    return inner


@pytest.mark.s03
def test_given_a_result_with_no_diffraction_when_zocalo_called_then_move_to_fallback(
    run_zocalo_with_dev_ispyb, zocalo_env
):
    fallback = np.array([1, 2, 3])
    zc, centre = run_zocalo_with_dev_ispyb("NO_DIFF", fallback)
    assert np.allclose(centre, fallback)


@pytest.mark.s03
def test_given_a_result_with_no_diffraction_ispyb_comment_updated(
    run_zocalo_with_dev_ispyb, zocalo_env, fetch_comment
):
    zc, centre = run_zocalo_with_dev_ispyb("NO_DIFF")

    comment = fetch_comment(zc.ispyb.ispyb_ids.data_collection_ids[0])
    assert "Found no diffraction." in comment


@pytest.mark.s03
def test_zocalo_adds_nonzero_comment_time(
    run_zocalo_with_dev_ispyb, zocalo_env, fetch_comment
):
    zc, centre = run_zocalo_with_dev_ispyb()

    comment = fetch_comment(zc.ispyb.ispyb_ids.data_collection_ids[0])
    assert comment[-29:-6] == "Zocalo processing took "
    assert float(comment[-6:-2]) > 0
    assert float(comment[-6:-2]) < 90


@pytest.mark.s03
def test_given_a_single_crystal_result_ispyb_comment_updated(
    run_zocalo_with_dev_ispyb, zocalo_env, fetch_comment
):
    zc, _ = run_zocalo_with_dev_ispyb()
    comment = fetch_comment(zc.ispyb.ispyb_ids.data_collection_ids[0])
    assert "Crystal 1" in comment
    assert "Strength" in comment
    assert "Size (grid boxes)" in comment


@pytest.mark.s03
def test_given_a_result_with_multiple_crystals_ispyb_comment_updated(
    run_zocalo_with_dev_ispyb, zocalo_env, fetch_comment
):
    zc, _ = run_zocalo_with_dev_ispyb("MULTI_X")

    comment = fetch_comment(zc.ispyb.ispyb_ids.data_collection_ids[0])
    assert "Crystal 1" and "Crystal 2" in comment
    assert "Strength" in comment
    assert "Position (grid boxes)" in comment


@pytest.mark.s03
def test_zocalo_returns_multiple_crystals(run_zocalo_with_dev_ispyb, zocalo_env):
    zc, _ = run_zocalo_with_dev_ispyb("MULTI_X")
    results = zc.zocalo_interactor.wait_for_result(
        zc.ispyb.ispyb_ids.data_collection_group_id
    )
    assert len(results) > 1
    assert results[0] == TEST_RESULT_LARGE[0]
    assert results[1] == TEST_RESULT_SMALL[0]