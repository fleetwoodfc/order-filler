# Processor for incoming HL7 messages (focused on ORM -> create Service Request).
# Updated to accept either a pyHL7 parsed message (package `hl7`) or raw ER7 text.
#
# Behavior:
# - Accepts ORM messages
# - Finds Patient (by PID-3 identifier fallback by name + DOB)
# - Extracts OBR (service code/name) and ORC/OBR details
# - Creates a Service Request and auto-submits it
# - Logs the raw HL7 message and processing status into HL7 Message Log doctype
# - Uses HL7 Service Code Mapping doctype to map service codes to template_dt/template_dn when available

import logging
from datetime import datetime
from typing import Optional

import frappe

logger = logging.getLogger("healthcare.ris.processor")
logger.setLevel(logging.INFO)


def log_hl7_message(raw_message, message_type=None, patient=None, status="Pending", note=None, error=None):
    try:
        log = frappe.new_doc("HL7 Message Log")
        log.raw_message = raw_message
        log.message_type = message_type or ""
        if patient:
            log.patient = patient
        log.status = status
        log.note = note or ""
        if error:
            log.error = error
        log.insert(ignore_permissions=True)
        return log
    except Exception:
        logger.exception("Failed to create HL7 Message Log entry")
        return None


def _normalize_dob(dob_value):
    """
    Convert HL7 PID-7 YYYYMMDD (or other common forms) to ISO date YYYY-MM-DD.
    If parsing fails, return the original value.
    """
    if not dob_value:
        return None
    try:
        # Common HL7 date format is YYYYMMDD
        if len(dob_value) >= 8 and dob_value.isdigit():
            dt = datetime.strptime(dob_value[:8], "%Y%m%d")
            return dt.date().isoformat()
    except Exception:
        pass
    return dob_value


# ---------- Helpers to read fields from either a pyHL7 parsed message or raw ER7 text ----------
def _find_segment_pyhl7(message, seg_name: str):
    """
    Given a parsed pyHL7 message (hl7.parse result), return the first segment list whose name matches seg_name.
    """
    if message is None:
        return None
    for seg in message:
        try:
            if str(seg[0]) == seg_name:
                return seg
        except Exception:
            continue
    return None


def _get_field_from_segment_pyhl7(seg, field_num: int) -> Optional[str]:
    """
    Return the (ER7) string of field number `field_num` from a pyHL7 segment.
    Note: field_num is the HL7 field number (1-based). Because pyHL7 segments include the segment name at index 0,
    the field value is at index == field_num (e.g., PID-3 -> seg[3]).
    """
    if not seg:
        return None
    try:
        # seg[field_num] may be a nested structure; convert to string
        if len(seg) > field_num:
            val = seg[field_num]
            return str(val) if val is not None else None
    except Exception:
        pass
    return None


def _parse_pid_from_raw(raw_text: str) -> dict:
    """
    Extract common PID fields from raw ER7 text using simple string splitting.
    Returns a dict with keys: identifier, name, dob, gender, address, phone
    """
    result = {}
    if not raw_text:
        return result
    try:
        for line in raw_text.split("\r"):
            if line.startswith("PID"):
                fields = line.split("|")
                # HL7 PID field numbers (1-based): PID-3 => index 3, PID-5 => index 5, PID-7 => index 7, PID-8 => index 8
                identifier = None
                if len(fields) > 3 and fields[3]:
                    identifier = fields[3].split("^")[0]
                name = fields[5].replace("^", " ") if len(fields) > 5 and fields[5] else None
                dob = fields[7] if len(fields) > 7 and fields[7] else None
                gender = fields[8] if len(fields) > 8 and fields[8] else None
                address = fields[11].replace("^", " ") if len(fields) > 11 and fields[11] else None
                phone = fields[13] if len(fields) > 13 and fields[13] else None
                result.update({
                    "identifier": identifier,
                    "name": name,
                    "dob": _normalize_dob(dob),
                    "gender": gender,
                    "address": address,
                    "phone": phone,
                })
                break
    except Exception:
        logger.exception("Failed to parse PID from raw text")
    return result


# ---------- Main patient lookup/create that supports both representations ----------
def get_patient_by_pid(pid_segment_or_message, create_if_missing=False, raw_message_text: Optional[str] = None):
    """
    pid_segment_or_message: either a pyHL7 segment object (segment list), or an hl7apy PID segment, or None.
    If pid_segment_or_message is None, raw_message_text will be parsed to find PID fields.
    If create_if_missing=True and identifying info is available, a Patient record will be created.
    """
    try:
        identifier = None
        name = None
        dob_value = None

        # 1) If given a pyHL7 message (list-like), locate PID seg and extract
        if pid_segment_or_message is not None:
            # Handle a pyHL7 segment (list) or a parsed message where first arg may be entire message
            if isinstance(pid_segment_or_message, list) or hasattr(pid_segment_or_message, "__iter__"):
                # If it's a full message (list of segments), find PID segment
                pid_seg = None
                if pid_segment_or_message and len(pid_segment_or_message) > 0 and str(pid_segment_or_message[0][0]) != "PID":
                    # probably a full message - find PID
                    pid_seg = _find_segment_pyhl7(pid_segment_or_message, "PID")
                else:
                    # assume pid_segment_or_message is the PID segment
                    pid_seg = pid_segment_or_message
                if pid_seg:
                    identifier = _get_field_from_segment_pyhl7(pid_seg, 3)
                    if identifier:
                        identifier = identifier.split("^")[0]
                    name = _get_field_from_segment_pyhl7(pid_seg, 5)
                    if name:
                        name = name.replace("^", " ")
                    dob_value = _get_field_from_segment_pyhl7(pid_seg, 7)
                    if dob_value:
                        dob_value = _normalize_dob(dob_value)
            else:
                # Could be an hl7apy object; fall back to the original behavior by trying to access attributes.
                try:
                    if hasattr(pid_segment_or_message, "PID3"):
                        pid3_text = pid_segment_or_message.PID3.to_er7()
                        if pid3_text:
                            identifier = pid3_text.split("^")[0]
                    if hasattr(pid_segment_or_message, "PID5"):
                        name = pid_segment_or_message.PID5.to_er7().replace("^", " ")
                    dob = getattr(pid_segment_or_message, "PID7", None)
                    dob_value = dob.value if dob else None
                except Exception:
                    # ignore and fall back to raw parsing below
                    identifier = None

        # 2) If still missing and raw_message_text provided, parse raw text
        if not identifier and raw_message_text:
            parsed_pid = _parse_pid_from_raw(raw_message_text)
            identifier = identifier or parsed_pid.get("identifier")
            name = name or parsed_pid.get("name")
            dob_value = dob_value or parsed_pid.get("dob")

        # Lookup by identifier first
        if identifier:
            patients = frappe.get_all(
                "Patient", filters={"patient_identifier": identifier}, fields=["name"]
            )
            if patients:
                return frappe.get_doc("Patient", patients[0].name)

        # Lookup by name + dob
        if name:
            candidates = frappe.get_all(
                "Patient", filters={"patient_name": name, "dob": dob_value}, fields=["name"]
            )
            if candidates:
                return frappe.get_doc("Patient", candidates[0].name)

        # Create patient if requested and we have enough info
        if create_if_missing:
            # If raw_message_text present, prefer its parsed fields for phone/address/gender
            phone = None
            address = None
            gender = None
            if raw_message_text:
                parsed_pid = _parse_pid_from_raw(raw_message_text)
                phone = parsed_pid.get("phone")
                address = parsed_pid.get("address")
                gender = parsed_pid.get("gender")
            try:
                p = frappe.new_doc("Patient")
                p.patient_name = name or identifier or "Unknown Patient"
                if identifier:
                    p.patient_identifier = identifier
                if dob_value:
                    p.dob = dob_value
                if gender:
                    p.gender = gender
                if address:
                    p.address = address
                if phone:
                    p.phone = phone
                p.insert(ignore_permissions=True)
                logger.info("Created new Patient %s from PID data", getattr(p, "name", p.patient_name))
                return p
            except Exception:
                logger.exception("Failed to create Patient from PID data")
                return None

    except Exception:
        logger.exception("Error finding patient from PID")
    return None


def extract_order_from_message(message_or_raw: Optional[object], raw_message_text: Optional[str] = None):
    """
    Extract a small set of order fields from either a parsed pyHL7 message or raw ER7 string.
    Returns dict with keys: universal_service_id, service_code, service_name, placer_order_number, filler_order_number, obs_datetime, practitioner
    """
    info = {}
    try:
        # If we received a pyHL7 parsed message, try to locate OBR/ORC segments
        pid_like = False
        if message_or_raw is not None and isinstance(message_or_raw, list):
            # pyHL7 parsed message
            # find ORC and OBR segments
            orc_seg = _find_segment_pyhl7(message_or_raw, "ORC")
            obr_seg = _find_segment_pyhl7(message_or_raw, "OBR")
            if orc_seg:
                info["placer_order_number"] = _get_field_from_segment_pyhl7(orc_seg, 2)
                info["filler_order_number"] = _get_field_from_segment_pyhl7(orc_seg, 3)
            if obr_seg:
                usi = _get_field_from_segment_pyhl7(obr_seg, 4)
                if usi:
                    info["universal_service_id"] = usi
                    parts = usi.split("^")
                    info["service_code"] = parts[0] if len(parts) > 0 else None
                    info["service_name"] = parts[1] if len(parts) > 1 else None
                info["obs_datetime"] = _get_field_from_segment_pyhl7(obr_seg, 7)
                provider = _get_field_from_segment_pyhl7(obr_seg, 16)
                if provider:
                    info["practitioner"] = provider
            return info

        # Fallback: parse raw ER7 text (if provided)
        if raw_message_text:
            try:
                for line in raw_message_text.split("\r"):
                    if line.startswith("ORC") and "placer_order_number" not in info:
                        f = line.split("|")
                        if len(f) > 2:
                            info["placer_order_number"] = f[2]
                        if len(f) > 3:
                            info["filler_order_number"] = f[3]
                    if line.startswith("OBR"):
                        f = line.split("|")
                        if len(f) > 4 and not info.get("universal_service_id"):
                            usi = f[4]
                            info["universal_service_id"] = usi
                            parts = usi.split("^")
                            info["service_code"] = parts[0] if len(parts) > 0 else None
                            info["service_name"] = parts[1] if len(parts) > 1 else None
                        if len(f) > 7:
                            info["obs_datetime"] = f[7]
                        if len(f) > 16:
                            info["practitioner"] = f[16]
                return info
            except Exception:
                logger.exception("Error extracting order info from raw text")
                return info

    except Exception:
        logger.exception("Error extracting order info")
    return info


def lookup_service_mapping(service_code):
    """Look up HL7 Service Code Mapping doctype to find template_dt/template_dn"""
    try:
        if not service_code:
            return None
        mapping = frappe.get_all(
            "HL7 Service Code Mapping",
            filters={"service_code": service_code},
            fields=["template_dt", "template_dn"],
            limit_page_length=1,
        )
        if mapping:
            return mapping[0]
    except Exception:
        logger.exception("Error looking up service code mapping")
    return None


def create_service_request(patient_doc, order_info, raw_message=None):
    """
    Create and auto-submit Service Request.
    Uses HL7 Service Code Mapping when available.
    """
    try:
        if not patient_doc:
            logger.warning("No patient doc supplied for SR creation")
            return None

        sr = frappe.new_doc("Service Request")
        sr.patient = patient_doc.name
        sr.order_date = frappe.utils.now_datetime()

        # Mapping: prefer configured mapping if present
        service_code = order_info.get("service_code")
        mapping = lookup_service_mapping(service_code)
        if mapping:
            sr.template_dt = mapping.get("template_dt") or "Lab Test Template"
            sr.template_dn = mapping.get("template_dn") or (order_info.get("service_name") or service_code)
        else:
            sr.template_dt = "Lab Test Template"
            sr.template_dn = order_info.get("service_name") or service_code or "Imported from HL7"

        comments = []
        if order_info.get("placer_order_number"):
            comments.append(f"Placer: {order_info.get('placer_order_number')}")
        if order_info.get("filler_order_number"):
            comments.append(f"Filler: {order_info.get('filler_order_number')}")
        comments.append(f"Imported via HL7 ORM - {order_info.get('universal_service_id')}")
        sr.order_description = " | ".join(comments)

        pr_field = order_info.get("practitioner")
        if pr_field:
            parts = pr_field.split("^")
            candidate_id = parts[0] if parts else None
            if candidate_id:
                practitioners = frappe.get_all(
                    "Healthcare Practitioner",
                    filters={"practitioner_identifier": candidate_id},
                    fields=["name"],
                )
                if practitioners:
                    sr.practitioner = practitioners[0].name

        sr.insert(ignore_permissions=True)
        try:
            sr.submit()
            logger.info("Service Request %s created and submitted for patient %s", sr.name, patient_doc.name)
            # Log success
            if raw_message:
                log_hl7_message(raw_message=raw_message, message_type="ORM", patient=patient_doc.name, status="Processed", note=f"Service Request {sr.name} created")
        except Exception as e:
            logger.exception("Failed to auto-submit Service Request %s; left as draft", sr.name)
            if raw_message:
                log_hl7_message(raw_message=raw_message, message_type="ORM", patient=patient_doc.name, status="Failed", error=str(e))
        return sr
    except Exception:
        logger.exception("Failed to create Service Request")
        if raw_message:
            log_hl7_message(raw_message=raw_message, message_type="ORM", status="Failed", error="create_service_request exception")
        return None


def _get_msg_type_from_msh_er7(msh_er7_line: str) -> str:
    """
    parse MSH ER7 line and return the MSH-9 field (message type).
    """
    try:
        parts = msh_er7_line.split("|")
        if len(parts) >= 9:
            return parts[8] or ""
    except Exception:
        pass
    return ""


def get_msg_type(message_or_raw, raw_message_text: Optional[str] = None) -> str:
    """
    Robust extraction of MSH-9 (message type) from either a pyHL7 parsed message or raw ER7 text.
    """
    try:
        # If pyHL7 message provided
        if isinstance(message_or_raw, list):
            msh = _find_segment_pyhl7(message_or_raw, "MSH")
            if msh:
                # Using HL7 numbering: MSH-9 is field number 9 -> index 9 in the pyHL7 segment list
                val = _get_field_from_segment_pyhl7(msh, 9)
                if val:
                    return val
        # If raw ER7 provided (or parsed not available), use raw text
        if raw_message_text:
            for line in raw_message_text.split("\r"):
                if line.startswith("MSH"):
                    return _get_msg_type_from_msh_er7(line)
    except Exception:
        logger.exception("Failed to extract message type")
    return ""


def process_hl7_message(message, raw_message_text: Optional[str] = None):
    """
    Entry point for listener. Returns True on success.
    message: parsed pyHL7 message (list) OR an hl7apy message OR None
    raw_message_text: optional raw HL7 payload (ER7) for parsing/logging
    """
    try:
        msg_type = get_msg_type(message, raw_message_text=raw_message_text)
        if raw_message_text:
            # create an initial log entry
            log = log_hl7_message(raw_message_text, message_type=msg_type, status="Pending")
        else:
            log = None

        if not msg_type.startswith("ORM"):
            logger.info("Ignoring non-ORM message: %s", msg_type)
            if log:
                log.status = "Processed"
                log.note = "Ignored non-ORM message"
                log.save()
            return False

        # For patient lookup, prefer parsed PID (pyHL7) else parse raw text
        pid_segment = None
        if isinstance(message, list):
            pid_segment = _find_segment_pyhl7(message, "PID")
        patient_doc = get_patient_by_pid(pid_segment, create_if_missing=True, raw_message_text=raw_message_text)
        if not patient_doc:
            logger.warning("Patient not found; cannot create Service Request")
            if log:
                log.status = "Failed"
                log.error = "Patient not found"
                log.save()
            return False

        order_info = extract_order_from_message(message, raw_message_text=raw_message_text)
        sr = create_service_request(patient_doc, order_info, raw_message=raw_message_text)
        if sr:
            return True
        else:
            return False
    except Exception:
        logger.exception("Error processing HL7 message")
        if raw_message_text:
            log_hl7_message(raw_message_text, status="Failed", error="processing exception")
        return False