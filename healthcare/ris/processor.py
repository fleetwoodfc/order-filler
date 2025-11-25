# Processor for incoming HL7 messages (focused on ORM -> create Service Request).

Behavior:
- Accepts ORM messages
- Finds Patient (by PID-3 identifier fallback by name + DOB)
- Extracts OBR (service code/name) and ORC/OBR details
- Creates a Service Request and auto-submits it
- Logs the raw HL7 message and processing status into HL7 Message Log doctype
- Uses HL7 Service Code Mapping doctype to map service codes to template_dt/template_dn when available

Adjust field mappings below if your site uses different field names.

import logging

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

def get_patient_by_pid(pid_segment):
    """
    pid_segment: hl7apy PID segment object
    Strategy:
    - use PID-3 (patient identifiers). Try first identifier value.
    - fallback: PID-5 (name) + PID-7 (dob)
    """
    try:
        if not pid_segment:
            return None
        # PID3
        identifier = None
        if hasattr(pid_segment, "PID3"):
            pid3_text = pid_segment.PID3.to_er7()
            if pid3_text:
                identifier = pid3_text.split("^")[0]
        if identifier:
            patients = frappe.get_all(
                "Patient", filters={"patient_identifier": identifier}, fields=["name"]
            )
            if patients:
                return frappe.get_doc("Patient", patients[0].name)
        # fallback: name + dob
        name = None
        if hasattr(pid_segment, "PID5"):
            name = pid_segment.PID5.to_er7().replace("^", " ")
        dob = getattr(pid_segment, "PID7", None)
        dob_value = dob.value if dob else None
        if name:
            candidates = frappe.get_all(
                "Patient", filters={"patient_name": name, "dob": dob_value}, fields=["name"]
            )
            if candidates:
                return frappe.get_doc("Patient", candidates[0].name)
    except Exception:
        logger.exception("Error finding patient from PID")
    return None

def extract_order_from_message(message):
    """
    Extract a small set of fields from ORC/OBR:
    - service_code, service_name from OBR-4 (universal service id)
    - placer/filler order numbers
    - requested datetime (OBR-7)
    - ordering provider (OBR-16 or ORC-12)
    """
    info = {}
    try:
        orc = message.ORC if hasattr(message, "ORC") else None
        if orc:
            info["placer_order_number"] = getattr(orc, "ORC2", None) and orc.ORC2.to_er7()
            info["filler_order_number"] = getattr(orc, "ORC3", None) and orc.ORC3.to_er7()

        obr = message.OBR if hasattr(message, "OBR") else None
        if obr:
            if hasattr(obr, "OBR4"):
                usi = obr.OBR4.to_er7()
                info["universal_service_id"] = usi
                parts = usi.split("^")
                info["service_code"] = parts[0] if len(parts) > 0 else None
                info["service_name"] = parts[1] if len(parts) > 1 else None
            info["obs_datetime"] = getattr(obr, "OBR7", None) and obr.OBR7.value
            provider = getattr(obr, "OBR16", None)
            if provider:
                info["practitioner"] = obr.OBR16.to_er7()
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

def process_hl7_message(message, raw_message_text=None):
    """
    Entry point for listener. Returns True on success.
    raw_message_text: optional raw HL7 payload for logging
    """
    try:
        msg_type = message.MSH.MSH9.to_er7() if hasattr(message, "MSH") and hasattr(message.MSH, "MSH9") else ""
        if raw_message_text:
            # create an initial log entry
            log = log_hl7_message(raw_message_text, message_type=msg_type, status="Pending")
        if not msg_type.startswith("ORM"):
            logger.info("Ignoring non-ORM message: %s", msg_type)
            if raw_message_text and log:
                log.status = "Processed"
                log.note = "Ignored non-ORM message"
                log.save()
            return False

        pid = message.PID if hasattr(message, "PID") else None
        patient_doc = get_patient_by_pid(pid)
        if not patient_doc:
            logger.warning("Patient not found; cannot create Service Request")
            if raw_message_text and log:
                log.status = "Failed"
                log.error = "Patient not found"
                log.save()
            return False

        order_info = extract_order_from_message(message)
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