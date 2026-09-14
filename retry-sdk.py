#!/usr/bin/env python3
"""
retry-sdk.py — poll OCI for Always Free ARM (Ampere A1) capacity and launch the
instant it appears. Uses the official OCI Python SDK.

Setup:
    pip install oci
    # reuse the SAME credential file the OCI CLI uses (~/.oci/config)

Run forever (always-on host):   python retry-sdk.py
Run a single sweep (CI/cron):   python retry-sdk.py --once

Exit codes (for CI):
    0  = launched, OR no capacity yet (both are "fine" outcomes)
    1  = a real error (bad credentials, wrong OCID, non-arm image, ...)
"""
import sys
import time
import logging

import oci

# --------------------------- EDIT THESE ---------------------------
COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaapcxrvac7jqbc7nbbdwznq72m4c3lj6uvu5ztluqch7lrzyloc5tq"  # or the tenant OCID
SUBNET_ID      = "ocid1.vcn.oc1.ap-kulai-2.amaaaaaasrq4isia7u3whevlmmjmyx5bydqvygwewu3acfvtbzlkz64icyjq"
IMAGE_ID       = "ocid1.image.oc1.ap-kulai-2.aaaaaaaaqjq4j22krb36x43ptnejsyvrc25qcxxlwuoqr34cati3o7sixezq"        # arm64 image
SHAPE          = "VM.Standard.A1.Flex"
OCPUS          = 2        # Always Free limit = 2 OCPU / 12 GB (since Jun 2026)
MEMORY_GB      = 12
DISPLAY_NAME   = "free-arm"
SLEEP_SECONDS  = 90       # wait between full sweeps (only used in loop mode)
AVAILABILITY_DOMAINS = [] # leave empty to auto-discover every AD in the region
CHECK_EXISTING = True     # skip work if a matching A1 instance already exists
# ------------------------------------------------------------------

ONCE = "--once" in sys.argv

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oci-arm")

# Substrings that mean "not now, try again later" rather than a config problem.
BENIGN = ("capacity", "limit", "quota", "throttl", "too many", "not available")

config = oci.config.from_file()          # ~/.oci/config, DEFAULT profile
oci.config.validate_config(config)

compartment = (COMPARTMENT_ID
               if COMPARTMENT_ID and "CHANGEME" not in COMPARTMENT_ID
               else config["tenancy"])

identity = oci.identity.IdentityClient(config)
compute = oci.core.ComputeClient(config)

if AVAILABILITY_DOMAINS:
    ads = AVAILABILITY_DOMAINS
else:
    ads = [ad.name for ad in
           identity.list_availability_domains(
               compartment_id=config["tenancy"]).data]

log.info("region=%s  ADs=%s", config["region"], ads)
log.info("target shape=%s  %s OCPU / %s GB", SHAPE, OCPUS, MEMORY_GB)


def build_details(ad):
    return oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment,
        availability_domain=ad,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS, memory_in_gbs=MEMORY_GB),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=IMAGE_ID),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID, assign_public_ip=True),
        display_name=DISPLAY_NAME,
    )


def already_have_one():
    """True if an A1 instance already exists in this compartment."""
    try:
        insts = compute.list_instances(compartment_id=compartment).data
    except Exception as e:                       # noqa: BLE001
        log.warning("could not list instances: %s", e)
        return False
    for i in insts:
        if (getattr(i, "shape", "") == SHAPE
                and i.lifecycle_state not in ("TERMINATED", "TERMINATING")):
            log.info("existing instance: %s (%s) state=%s",
                     i.display_name, i.id, i.lifecycle_state)
            return True
    return False


def is_benign(err):
    blob = " ".join(str(x).lower() for x in
                    (err.code, err.message, getattr(err, "status", "")))
    return any(k in blob for k in BENIGN)


if CHECK_EXISTING and already_have_one():
    log.info("A matching %s instance already exists - nothing to do.", SHAPE)
    sys.exit(0)

real_error = False
attempt = 0
while True:
    attempt += 1
    for ad in ads:
        log.info("sweep #%d  trying AD=%s", attempt, ad)
        try:
            resp = compute.launch_instance(build_details(ad))
            log.info("SUCCESS: launched %s  state=%s",
                     resp.data.id, resp.data.lifecycle_state)
            sys.exit(0)
        except oci.exceptions.ServiceError as e:
            if is_benign(e):
                log.info("  -> no capacity yet (%s)", e.code)
            else:
                # Usually a misconfiguration: bad OCID, non-arm image, shape
                # not available in this AD, missing IAM policy, etc.
                log.error("  !! error %s %s: %s", e.status, e.code, e.message)
                real_error = True
        except Exception as e:                   # noqa: BLE001
            log.error("  !! unexpected: %s", e)
            real_error = True

    if ONCE:
        log.info("--once: sweep complete (%s)",
                 "finished with errors" if real_error else "no capacity yet")
        sys.exit(1 if real_error else 0)

    time.sleep(SLEEP_SECONDS)
