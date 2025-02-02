from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, Optional

from hyperion.external_interaction.callbacks.plan_reactive_callback import (
    PlanReactiveCallback,
)
from hyperion.external_interaction.ispyb.store_in_ispyb import IspybIds, StoreInIspyb
from hyperion.log import ISPYB_LOGGER, set_dcgid_tag
from hyperion.parameters.constants import (
    ISPYB_HARDWARE_READ_PLAN,
    ISPYB_TRANSMISSION_FLUX_READ_PLAN,
    SIM_ISPYB_CONFIG,
)
from hyperion.parameters.plan_specific.gridscan_internal_params import (
    GridscanInternalParameters,
)
from hyperion.parameters.plan_specific.rotation_scan_internal_params import (
    RotationInternalParameters,
)

if TYPE_CHECKING:
    from hyperion.external_interaction.ispyb.store_in_ispyb import StoreInIspyb


class BaseISPyBCallback(PlanReactiveCallback):
    def __init__(self) -> None:
        """Subclasses should run super().__init__() with parameters, then set
        self.ispyb to the type of ispyb relevant to the experiment and define the type
        for self.ispyb_ids."""
        super().__init__()
        self.params: GridscanInternalParameters | RotationInternalParameters | None = (
            None
        )
        self.ispyb: StoreInIspyb
        self.descriptors: Dict[str, dict] = {}
        self.ispyb_config = os.environ.get("ISPYB_CONFIG_PATH", SIM_ISPYB_CONFIG)
        if self.ispyb_config == SIM_ISPYB_CONFIG:
            ISPYB_LOGGER.warning(
                "Using dev ISPyB database. If you want to use the real database, please"
                " set the ISPYB_CONFIG_PATH environment variable."
            )
        self.uid_to_finalize_on: Optional[str] = None
        self.ispyb_ids: IspybIds = IspybIds()

    def activity_gated_start(self, doc: dict):
        if self.uid_to_finalize_on is None:
            self.uid_to_finalize_on = doc.get("uid")

    def activity_gated_descriptor(self, doc: dict):
        self.descriptors[doc["uid"]] = doc

    def activity_gated_event(self, doc: dict):
        """Subclasses should extend this to add a call to set_dcig_tag from
        hyperion.log"""
        ISPYB_LOGGER.debug("ISPyB handler received event document.")
        assert self.ispyb is not None, "ISPyB deposition wasn't initialised!"
        assert self.params is not None, "ISPyB handler didn't recieve parameters!"
        event_descriptor = self.descriptors[doc["descriptor"]]

        event_descriptor = self.descriptors[doc["descriptor"]]
        if event_descriptor.get("name") == ISPYB_HARDWARE_READ_PLAN:
            self.params.hyperion_params.ispyb_params.undulator_gap = doc["data"][
                "undulator_current_gap"
            ]
            self.params.hyperion_params.ispyb_params.synchrotron_mode = doc["data"][
                "synchrotron_machine_status_synchrotron_mode"
            ]
            self.params.hyperion_params.ispyb_params.slit_gap_size_x = doc["data"][
                "s4_slit_gaps_xgap"
            ]
            self.params.hyperion_params.ispyb_params.slit_gap_size_y = doc["data"][
                "s4_slit_gaps_ygap"
            ]

        if event_descriptor.get("name") == ISPYB_TRANSMISSION_FLUX_READ_PLAN:
            self.params.hyperion_params.ispyb_params.transmission_fraction = doc[
                "data"
            ]["attenuator_actual_transmission"]
            self.params.hyperion_params.ispyb_params.flux = doc["data"][
                "flux_flux_reading"
            ]

            ISPYB_LOGGER.info("Creating ispyb entry.")
            self.ispyb_ids = self.ispyb.begin_deposition()
            ISPYB_LOGGER.info(f"Recieved ISPYB IDs: {self.ispyb_ids}")

    def activity_gated_stop(self, doc: dict):
        """Subclasses must check that they are recieving a stop document for the correct
        uid to use this method!"""
        assert isinstance(
            self.ispyb, StoreInIspyb
        ), "ISPyB handler recieved stop document, but deposition object doesn't exist!"
        ISPYB_LOGGER.debug("ISPyB handler received stop document.")
        exit_status = (
            doc.get("exit_status") or "Exit status not available in stop document!"
        )
        reason = doc.get("reason") or ""

        set_dcgid_tag(None)
        try:
            self.ispyb.end_deposition(exit_status, reason)
        except Exception as e:
            ISPYB_LOGGER.warning(
                f"Failed to finalise ISPyB deposition on stop document: {doc} with exception: {e}"
            )

    def _append_to_comment(self, id: int, comment: str):
        assert isinstance(self.ispyb, StoreInIspyb)
        try:
            self.ispyb.append_to_comment(id, comment)
        except TypeError:
            ISPYB_LOGGER.warning(
                "ISPyB deposition not initialised, can't update comment."
            )
