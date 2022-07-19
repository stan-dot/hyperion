import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

import argparse

import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp
from bluesky import RunEngine
from bluesky.log import config_bluesky_logging
from bluesky.utils import ProgressBarManager
from ophyd.log import config_ophyd_logging

from src.artemis.devices.eiger import EigerDetector
from src.artemis.devices.fast_grid_scan import FastGridScan, set_fast_grid_scan_params
from src.artemis.devices.motors import I03Smargon
from src.artemis.devices.slit_gaps import SlitGaps
from src.artemis.devices.synchrotron import Synchrotron
from src.artemis.devices.undulator import Undulator
from src.artemis.devices.zebra import Zebra
from src.artemis.ispyb.store_in_ispyb import StoreInIspyb2D, StoreInIspyb3D
from src.artemis.nexus_writing.write_nexus import NexusWriter
from src.artemis.parameters import SIM_BEAMLINE, FullParameters
from src.artemis.zocalo_interaction import run_end, run_start, wait_for_result

config_bluesky_logging(file="/tmp/bluesky.log", level="DEBUG")
config_ophyd_logging(file="/tmp/ophyd.log", level="DEBUG")

# Tolerance for how close omega must start to 0
OMEGA_TOLERANCE = 0.1

# Clear odin errors and check initialised
# If in error clean up
# Setup beamline
# Store in ISPyB
# Start nxgen
# Start analysis run collection


def update_params_from_epics_devices(
    parameters: FullParameters,
    undulator: Undulator,
    synchrotron: Synchrotron,
    slit_gap: SlitGaps,
):
    parameters.ispyb_params.undulator_gap = yield from bps.rd(undulator.gap)
    parameters.ispyb_params.synchrotron_mode = yield from bps.rd(
        synchrotron.machine_status.synchrotron_mode
    )
    parameters.ispyb_params.slit_gap_size_x = yield from bps.rd(slit_gap.xgap)
    parameters.ispyb_params.slit_gap_size_y = yield from bps.rd(slit_gap.ygap)


@bpp.run_decorator()
def run_gridscan(
    fgs: FastGridScan,
    zebra: Zebra,
    eiger: EigerDetector,
    sample_motors: I03Smargon,
    undulator: Undulator,
    synchrotron: Synchrotron,
    slit_gap: SlitGaps,
    parameters: FullParameters,
):
    current_omega = yield from bps.rd(sample_motors.omega, default_value=0)
    assert abs(current_omega - parameters.detector_params.omega_start) < OMEGA_TOLERANCE
    assert (
        abs(current_omega) < OMEGA_TOLERANCE
    )  # This should eventually be removed, see #154

    yield from update_params_from_epics_devices(
        parameters, undulator, synchrotron, slit_gap
    )

    config = "config"
    ispyb = (
        StoreInIspyb3D(config)
        if parameters.grid_scan_params.is_3d_grid_scan
        else StoreInIspyb2D(config)
    )

    datacollection_ids, _, datacollection_group_id = ispyb.store_grid_scan(parameters)

    for id in datacollection_ids:
        run_start(id)

    # TODO: Check topup gate
    yield from set_fast_grid_scan_params(fgs, parameters.grid_scan_params)

    @bpp.stage_decorator([zebra, eiger, fgs])
    def do_fgs():
        yield from bps.kickoff(fgs)
        yield from bps.complete(fgs, wait=True)

    with NexusWriter(parameters):
        yield from do_fgs()

    current_time = ispyb.get_current_time_string()
    for id in datacollection_ids:
        ispyb.update_grid_scan_with_end_time_and_status(
            current_time,
            "DataCollection Successful",
            id,
            datacollection_group_id,
        )

    for id in datacollection_ids:
        run_end(id)

    xray_centre = wait_for_result(datacollection_group_id)
    xray_centre_motor_position = (
        parameters.grid_scan_params.grid_position_to_motor_position(xray_centre)
    )

    yield from bps.mv(sample_motors.omega, parameters.detector_params.omega_start)

    # After moving omega we need to wait for x, y and z to have finished moving too
    yield from bps.wait(sample_motors)

    yield from bps.mv(
        sample_motors.x,
        xray_centre_motor_position.x,
        sample_motors.y,
        xray_centre_motor_position.y,
        sample_motors.z,
        xray_centre_motor_position.z,
    )


def get_plan(parameters: FullParameters):
    """Create the plan to run the grid scan based on provided parameters.

    Args:
        parameters (FullParameters): The parameters to run the scan.

    Returns:
        Generator: The plan for the gridscan
    """
    fast_grid_scan = FastGridScan(
        name="fgs", prefix=f"{parameters.beamline}-MO-SGON-01:FGS:"
    )
    eiger = EigerDetector(
        parameters.detector_params,
        name="eiger",
        prefix=f"{parameters.beamline}-EA-EIGER-01:",
    )
    zebra = Zebra(name="zebra", prefix=f"{parameters.beamline}-EA-ZEBRA-01:")

    sample_motors = I03Smargon(
        name="sample_motors", prefix=f"{parameters.beamline}-MO-SGON-01:"
    )

    undulator = Undulator(
        name="undulator", prefix=f"{parameters.insertion_prefix}-MO-SERVC-01:"
    )
    synchrotron = Synchrotron(name="synchrotron")
    slit_gaps = SlitGaps(name="slit_gaps", prefix=f"{parameters.beamline}-AL-SLITS-04:")

    return run_gridscan(
        fast_grid_scan,
        zebra,
        eiger,
        sample_motors,
        undulator,
        synchrotron,
        slit_gaps,
        parameters,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--beamline",
        help="The beamline prefix this is being run on",
        default=SIM_BEAMLINE,
    )
    args = parser.parse_args()

    RE = RunEngine({})
    RE.waiting_hook = ProgressBarManager()

    parameters = FullParameters(beamline=args.beamline)

    RE(get_plan(parameters))
