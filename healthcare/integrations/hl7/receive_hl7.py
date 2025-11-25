"""
HL7 v2 Message Receiver for Radiology Order Filler

This module provides a whitelisted endpoint to receive HL7 v2 messages
(particularly ORM^O01 messages for radiology orders) and create the appropriate
DocTypes (RadiologyProcedureRequest and RadiologyAccession).

Implements IHE Radiology TF Appendix A clarifications:
- Accession Number vs Requested Procedure ID (RPID)
- RPID stored in external_request_id (from OBR-20 or OBR-4)
- Accession Number from OBR-18 or auto-generated if not provided

Key Features:
- Parse HL7 ORM messages (can be extended for other message types)
- Extract patient, order, and procedure information
- Create/update RadiologyProcedureRequest with RPID
- Create/link RadiologyAccession with accession number
- Return structured response including assigned accession numbers
"""

import frappe
from frappe import _
import logging
import json

logger = logging.getLogger("healthcare.integrations.hl7.receive_hl7")


@frappe.whitelist(allow_guest=False)
def receive_hl7(message, message_type=None):
    """
    Whitelisted endpoint to receive HL7 v2 messages.
    
    Args:
        message: HL7 v2 message string (pipe-delimited)
        message_type: Optional message type hint (e.g., "ORM^O01")
    
    Returns:
        dict with processing result including:
        - status: "success" or "error"
        - request_id: Created RadiologyProcedureRequest name
        - accession_number: Assigned or linked accession number
        - message: Status message
        - error: Error details if any
    """
    try:
        # Import hl7apy for parsing
        try:
            from hl7apy.parser import parse_message
        except ImportError:
            frappe.throw("hl7apy library not installed. Install with: pip install hl7apy")
        
        # Parse the message
        parsed_msg = parse_message(message)
        
        # Determine message type
        msg_type = message_type or get_message_type(parsed_msg)
        
        # Log the incoming message
        log_entry = log_hl7_message(
            raw_message=message,
            message_type=msg_type,
            status="Pending"
        )
        
        # Route based on message type
        if msg_type and msg_type.startswith("ORM"):
            result = process_orm_message(parsed_msg, message)
            
            # Update log with result
            if log_entry:
                log_entry.status = "Processed" if result.get("status") == "success" else "Failed"
                log_entry.note = result.get("message", "")
                if result.get("error"):
                    log_entry.error = str(result.get("error"))
                if result.get("patient"):
                    log_entry.patient = result.get("patient")
                log_entry.save(ignore_permissions=True)
                frappe.db.commit()
            
            return result
        else:
            error_msg = f"Unsupported message type: {msg_type}"
            if log_entry:
                log_entry.status = "Failed"
                log_entry.error = error_msg
                log_entry.save(ignore_permissions=True)
                frappe.db.commit()
            
            return {
                "status": "error",
                "message": error_msg
            }
    
    except Exception as e:
        logger.exception("Error processing HL7 message")
        frappe.log_error(
            message=str(e),
            title="HL7 Message Processing Error"
        )
        return {
            "status": "error",
            "message": "Failed to process HL7 message",
            "error": str(e)
        }


def get_message_type(parsed_msg):
    """Extract message type from parsed HL7 message."""
    try:
        if hasattr(parsed_msg, "MSH"):
            msh = parsed_msg.MSH
            if hasattr(msh, "MSH9"):
                msg_type = msh.MSH9.to_er7()
                return msg_type
    except Exception:
        logger.exception("Error extracting message type")
    return None


def log_hl7_message(raw_message, message_type=None, patient=None, status="Pending", note=None, error=None):
    """Create an HL7 Message Log entry."""
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
        frappe.db.commit()
        return log
    except Exception:
        logger.exception("Failed to create HL7 Message Log entry")
        return None


def process_orm_message(parsed_msg, raw_message):
    """
    Process ORM (Order Management) message.
    
    Extracts patient, order control, and procedure information from ORM message
    and creates RadiologyProcedureRequest and RadiologyAccession as needed.
    
    Args:
        parsed_msg: Parsed HL7 message object
        raw_message: Original raw HL7 message string
    
    Returns:
        dict with processing result
    """
    try:
        # Extract patient information
        patient = None
        if hasattr(parsed_msg, "PID"):
            patient = get_or_create_patient(parsed_msg.PID)
        
        if not patient:
            return {
                "status": "error",
                "message": "Could not identify patient from message"
            }
        
        # Extract order information
        order_info = extract_order_info(parsed_msg)
        
        if not order_info.get("external_request_id"):
            return {
                "status": "error",
                "message": "No external request ID (RPID) found in message"
            }
        
        # Check if request already exists
        existing_request = frappe.db.exists(
            "Radiology Procedure Request",
            {"external_request_id": order_info["external_request_id"]}
        )
        
        if existing_request:
            request = frappe.get_doc("Radiology Procedure Request", existing_request)
            action = "updated"
        else:
            # Create new procedure request
            request = frappe.new_doc("Radiology Procedure Request")
            request.patient = patient
            request.external_request_id = order_info["external_request_id"]
            request.placer_order_number = order_info.get("placer_order_number", "")
            request.filler_order_number = order_info.get("filler_order_number", "")
            request.service_code = order_info.get("service_code", "")
            request.service_name = order_info.get("service_name", "")
            request.ordering_provider = order_info.get("ordering_provider", "")
            request.requested_datetime = order_info.get("requested_datetime")
            request.procedure_priority = order_info.get("priority", "Routine")
            request.raw_hl7_message = raw_message
            request.status = "Pending"
            action = "created"
        
        # Handle accession number
        accession_number = order_info.get("accession_number")
        accession_result = None
        
        if accession_number:
            # Use provided accession number
            accession_result = handle_accession_from_message(
                request, patient, accession_number
            )
        else:
            # Check if auto-generation is enabled
            from healthcare.doctype.radiology_procedure_request.radiology_procedure_request import (
                should_auto_generate_accession,
                create_accession_for_request
            )
            
            if should_auto_generate_accession():
                # Save request first so it has a name
                request.insert(ignore_permissions=True)
                frappe.db.commit()
                
                # Generate and link accession
                accession = create_accession_for_request(request)
                request.radiology_accession = accession.name
                request.save(ignore_permissions=True)
                frappe.db.commit()
                
                accession_result = {
                    "accession_number": accession.accession_number,
                    "generated": True
                }
            else:
                # No accession handling - just save request
                request.insert(ignore_permissions=True)
                frappe.db.commit()
        
        # If not already saved, save now
        if not request.name:
            request.insert(ignore_permissions=True)
            frappe.db.commit()
        
        return {
            "status": "success",
            "message": f"Procedure request {action} successfully",
            "patient": patient,
            "request_id": request.name,
            "external_request_id": request.external_request_id,
            "accession_number": accession_result.get("accession_number") if accession_result else None,
            "accession_generated": accession_result.get("generated", False) if accession_result else False
        }
    
    except Exception as e:
        logger.exception("Error processing ORM message")
        return {
            "status": "error",
            "message": "Failed to process ORM message",
            "error": str(e)
        }


def get_or_create_patient(pid_segment):
    """
    Get or create patient from PID segment.
    
    Strategy:
    1. Look up by patient identifier (PID-3)
    2. Fallback to name + DOB match
    3. For now, return None if not found (don't auto-create)
    
    Args:
        pid_segment: PID segment from HL7 message
    
    Returns:
        Patient name (string) or None
    """
    try:
        # Try PID-3 patient identifier
        if hasattr(pid_segment, "PID3"):
            pid3_text = pid_segment.PID3.to_er7()
            if pid3_text:
                identifier = pid3_text.split("^")[0]
                if identifier:
                    patients = frappe.get_all(
                        "Patient",
                        filters={"patient_identifier": identifier},
                        fields=["name"]
                    )
                    if patients:
                        return patients[0].name
        
        # Fallback: name + DOB
        name = None
        if hasattr(pid_segment, "PID5"):
            name_parts = pid_segment.PID5.to_er7().split("^")
            # Format: Last^First^Middle
            if len(name_parts) >= 2:
                name = f"{name_parts[1]} {name_parts[0]}"  # First Last
            elif len(name_parts) == 1:
                name = name_parts[0]
        
        dob = None
        if hasattr(pid_segment, "PID7"):
            dob = pid_segment.PID7.value
        
        if name:
            filters = {"patient_name": name}
            if dob:
                filters["dob"] = dob
            
            patients = frappe.get_all("Patient", filters=filters, fields=["name"])
            if patients:
                return patients[0].name
    
    except Exception:
        logger.exception("Error getting patient from PID")
    
    return None


def extract_order_info(parsed_msg):
    """
    Extract order information from ORM message.
    
    Key fields:
    - OBR-20: External Request ID (RPID) - preferred
    - OBR-4: Universal Service ID (contains procedure code)
    - OBR-18: Accession Number (if provided by placer)
    - ORC-2: Placer Order Number
    - ORC-3: Filler Order Number
    - OBR-7: Requested DateTime
    - OBR-16: Ordering Provider
    - OBR-5: Priority
    
    Returns:
        dict with extracted order information
    """
    info = {}
    
    try:
        # Get ORC segment
        orc = None
        if hasattr(parsed_msg, "ORC"):
            orc = parsed_msg.ORC
        
        if orc:
            # ORC-2: Placer Order Number
            if hasattr(orc, "ORC2"):
                info["placer_order_number"] = orc.ORC2.to_er7()
            
            # ORC-3: Filler Order Number
            if hasattr(orc, "ORC3"):
                info["filler_order_number"] = orc.ORC3.to_er7()
        
        # Get OBR segment
        obr = None
        if hasattr(parsed_msg, "OBR"):
            obr = parsed_msg.OBR
        
        if obr:
            # OBR-20: Requested Procedure ID (RPID) - this is what we use as external_request_id
            if hasattr(obr, "OBR20"):
                rpid = obr.OBR20.to_er7()
                if rpid:
                    info["external_request_id"] = rpid
            
            # Fallback: use OBR-4 (service code) if RPID not provided
            if not info.get("external_request_id") and hasattr(obr, "OBR4"):
                service_id = obr.OBR4.to_er7()
                # Use placer order number + service code as RPID
                if info.get("placer_order_number"):
                    info["external_request_id"] = f"{info['placer_order_number']}_{service_id.split('^')[0]}"
            
            # OBR-4: Universal Service ID (service code^service name^coding system)
            if hasattr(obr, "OBR4"):
                usi = obr.OBR4.to_er7()
                parts = usi.split("^")
                info["service_code"] = parts[0] if len(parts) > 0 else None
                info["service_name"] = parts[1] if len(parts) > 1 else None
            
            # OBR-18: Accession Number (if provided by placer)
            if hasattr(obr, "OBR18"):
                accession = obr.OBR18.to_er7()
                if accession:
                    info["accession_number"] = accession
            
            # OBR-7: Requested DateTime
            if hasattr(obr, "OBR7"):
                info["requested_datetime"] = obr.OBR7.value
            
            # OBR-16: Ordering Provider
            if hasattr(obr, "OBR16"):
                provider = obr.OBR16.to_er7()
                if provider:
                    info["ordering_provider"] = provider
            
            # OBR-5: Priority
            if hasattr(obr, "OBR5"):
                priority_code = obr.OBR5.to_er7()
                priority_map = {
                    "R": "Routine",
                    "S": "Stat",
                    "A": "Asap",
                    "U": "Urgent"
                }
                info["priority"] = priority_map.get(priority_code, "Routine")
    
    except Exception:
        logger.exception("Error extracting order info")
    
    return info


def handle_accession_from_message(request, patient, accession_number):
    """
    Handle accession number provided in HL7 message.
    
    If accession already exists for same patient, link to it.
    Otherwise, create new accession with provided number.
    
    Args:
        request: RadiologyProcedureRequest document (may be unsaved)
        patient: Patient name
        accession_number: Accession number from message
    
    Returns:
        dict with accession_number and generated flag
    """
    try:
        # Check if accession exists
        if frappe.db.exists("Radiology Accession", accession_number):
            accession = frappe.get_doc("Radiology Accession", accession_number)
            
            # Verify patient matches
            if accession.patient != patient:
                logger.warning(
                    f"Accession {accession_number} exists but belongs to different patient"
                )
                # Generate new accession instead
                from healthcare.doctype.radiology_procedure_request.radiology_procedure_request import (
                    create_accession_for_request
                )
                
                if not request.name:
                    request.insert(ignore_permissions=True)
                    frappe.db.commit()
                
                new_accession = create_accession_for_request(request)
                request.radiology_accession = new_accession.name
                request.save(ignore_permissions=True)
                frappe.db.commit()
                
                return {
                    "accession_number": new_accession.accession_number,
                    "generated": True
                }
            else:
                # Link to existing accession
                if not request.name:
                    request.insert(ignore_permissions=True)
                    frappe.db.commit()
                
                request.radiology_accession = accession.name
                request.save(ignore_permissions=True)
                
                # Add to accession's request table
                existing_link = False
                for link in accession.requests:
                    if link.procedure_request == request.name:
                        existing_link = True
                        break
                
                if not existing_link:
                    accession.append("requests", {
                        "procedure_request": request.name,
                        "external_request_id": request.external_request_id,
                        "service_name": request.service_name
                    })
                    accession.save(ignore_permissions=True)
                
                frappe.db.commit()
                
                return {
                    "accession_number": accession_number,
                    "generated": False
                }
        else:
            # Create new accession with provided number
            accession = frappe.new_doc("Radiology Accession")
            accession.accession_number = accession_number
            accession.patient = patient
            from frappe.utils import nowdate
            accession.study_date = nowdate()
            accession.status = "Scheduled"
            accession.insert(ignore_permissions=True)
            frappe.db.commit()
            
            # Link request to accession
            if not request.name:
                request.insert(ignore_permissions=True)
                frappe.db.commit()
            
            request.radiology_accession = accession.name
            request.save(ignore_permissions=True)
            
            # Add to accession's request table
            accession.append("requests", {
                "procedure_request": request.name,
                "external_request_id": request.external_request_id,
                "service_name": request.service_name
            })
            accession.save(ignore_permissions=True)
            frappe.db.commit()
            
            return {
                "accession_number": accession_number,
                "generated": False
            }
    
    except Exception:
        logger.exception("Error handling accession from message")
        raise
