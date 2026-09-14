#!/usr/bin/env python3
"""
retry-sdk.py — poll OCI for Always Free ARM (Ampere A1) capacity and launch the
instant it appears. Uses the official OCI Python SDK.

Modes:
    python retry-sdk.py            # loop forever (always-on host)
    python retry-sdk.py --once     # one sweep, then exit (GitHub Actions)
    python retry-sdk.py --check    # only validate config/OCIDs, then exit

Exit codes:
    0 = launched OR no capacity yet (both fine) / check passed
    1 = a real error (bad credentials, wrong OCID, non-arm image, ...)
"""
import sys
import time
import logging

import oci

# --------------------------- EDIT THESE ---------------------------
COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaapcxrvac7jqbc7nbbdwznq72m4c3lj6uvu5ztluqch7lrzyloc5tq"
# Subnet OCID — MUST start with "ocid1.subnet." (a VCN starts with "ocid1.vcn.")
SUBNET_ID      = "ocid1.vcn.oc1.ap-kulai-2.amaaaaaasrq4isia7u3whevlmmjmyx5bydqvygwewu3acfvtbzlkz64icyjq"
IMAGE_ID       = "ocid1.image.oc1.ap-kulai-2.aaaaaaaaqjq4j22krb36x43ptnejsyvrc25qcxxlwuoqr34cati3o7sixezq"
SHAPE          = "VM.Standard.A1.Flex"
OCPUS          = 2        # Always Free limit = 2 OCPU / 12 GB (since Jun 2026)
MEMORY_GB      = 12
DISPLAY_NAME   = "free-arm"
SLEEP_SECONDS  = 90       # wait between full sweeps (loop mode only)
AVAILABILITY_DOMAINS = [] # leave empty to auto-discover every AD in the region
CHECK_EXISTING = True     # skip work if a matching A1 instance already exists
# ------------------------------------------------------------------

ONCE  = "--once" in sys.argv
CHECK = "--check" in sys.argv

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
compute  = oci.core.ComputeClient(config)
network  = oci.core.VirtualNetworkClient(config)

log.info("region=%s", config["region"])


def preflight():
    """Validate every OCID up-front, so failures are specific, not a vague 404."""
    ok = True

    # -- Compartment / tenancy --------------------------------------------
    if (COMPARTMENT_ID.startswith("ocid1.compartment.")
            or COMPARTMENT_ID.startswith("ocid1.tenancy.")):
        try:
            obj = (identity.get_tenancy(compartment).data
                   if compartment.startswith("ocid1.tenancy")
                   else identity.get_compartment(compartment).data)
            log.info("OK  compartment: %s", obj.name)
        except Exception as e:                   # noqa: BLE001
            ok = False
            log.error("BAD COMPARTMENT_ID: %s", e)
    else:
        ok = False
        log.error("COMPARTMENT_ID is not a compartment/tenancy OCID: '%s...'",
                  COMPARTMENT_ID[:32])

    # -- Image -------------------------------------------------------------
    if IMAGE_ID.startswith("ocid1.image."):
        try:
            img = compute.get_image(IMAGE_ID).data
            log.info("OK  image: %s (%s)", img.display_name, img.operating_system)
            low = (img.display_name or "").lower()
            if "aarch64" not in low and "arm" not in low:
                log.warning("    -> image name doesn't look arm64; A1.Flex needs "
                            "an aarch64 image")
        except Exception as e:                   # noqa: BLE001
            ok = False
            log.error("BAD IMAGE_ID (%s...): %s", IMAGE_ID[:32], e)
            log.error("    -> platform image OCIDs are REGION-SPECIFIC. Get the "
                      "aarch64 Ubuntu OCID for region '%s' from", config["region"])
            log.error("       https://docs.oracle.com/iaas/images/")
    else:
        ok = False
        log.error("IMAGE_ID is not an image OCID: '%s...'", IMAGE_ID[:32])

    # -- Subnet (the usual culprit) ---------------------------------------
    if SUBNET_ID.startswith("ocid1.subnet."):
        try:
            sn = network.get_subnet(SUBNET_ID).data
            log.info("OK  subnet: %s (vcn=%s)", sn.display_name, sn.vcn_id)
        except Exception as e:                   # noqa: BLE001
            ok = False
            log.error("BAD SUBNET_ID (%s...): %s", SUBNET_ID[:32], e)
            log.error("    -> subnets are region-specific; use a PUBLIC subnet in "
                      "region '%s'", config["region"])
    else:
        ok = False
        kind = SUBNET_ID.split(".")[0] if SUBNET_ID else "empty"
        log.error("SUBNET_ID is not a subnet OCID (got '%s...'). A subnet starts "
                  "with 'ocid1.subnet.'", SUBNET_ID[:32])
        log.error("    -> you pasted a '%s' OCID. In the Console go to "
                  "Networking -> Virtual Cloud Networks -> <your VCN> -> Subnets "
                  "-> click the subnet -> copy ITS OCID.", kind)

    return ok


if not preflight():
    log.error("Preflight failed: fix the OCID(s) flagged above and commit again.")
    sys.exit(1)

if CHECK:
    log.info("--check: all OCIDs OK.")
    sys.exit(0)

# ---- Discover availability domains -----------------------------------
if AVAILABILITY_DOMAINS:
    ads = AVAILABILITY_DOMAINS
else:
    ads = [ad.name for ad in
           identity.list_availability_domains(
               compartment_id=config["tenancy"]).data]

log.info("ADs=%s", ads)
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
