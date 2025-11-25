"""
FHIR Mapper for Radiology Order Filler

This module provides mapping functions to convert between Frappe DocTypes
(RadiologyProcedureRequest, RadiologyAccession) and FHIR resources
(ProcedureRequest, ImagingStudy).

Supports:
- RadiologyProcedureRequest -> FHIR ProcedureRequest
- RadiologyAccession -> FHIR ImagingStudy
- Basic reverse mapping for incoming FHIR resources

Reference: FHIR R4 specification
- ProcedureRequest: http://hl7.org/fhir/R4/procedurerequest.html
- ImagingStudy: http://hl7.org/fhir/R4/imagingstudy.html
"""

import frappe
import json
from datetime import datetime


def request_to_fhir_procedurerequest(request_doc):
    """
    Convert RadiologyProcedureRequest to FHIR ProcedureRequest resource.
    
    Args:
        request_doc: RadiologyProcedureRequest document or dict
    
    Returns:
        dict representing FHIR ProcedureRequest resource
    """
    if isinstance(request_doc, str):
        request_doc = frappe.get_doc("Radiology Procedure Request", request_doc)
    
    # Build FHIR ProcedureRequest resource
    procedure_request = {
        "resourceType": "ProcedureRequest",
        "id": request_doc.name,
        "identifier": [
            {
                "system": "urn:oid:radiology-procedure-request",
                "value": request_doc.name
            }
        ],
        "status": map_status_to_fhir(request_doc.status),
        "intent": "order",
        "subject": {
            "reference": f"Patient/{request_doc.patient}",
            "display": request_doc.patient
        }
    }
    
    # Add external request ID (RPID) as identifier
    if request_doc.external_request_id:
        procedure_request["identifier"].append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "RPID",
                    "display": "Requested Procedure ID"
                }]
            },
            "value": request_doc.external_request_id
        })
    
    # Add placer order number
    if request_doc.placer_order_number:
        procedure_request["identifier"].append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "PLAC",
                    "display": "Placer Order Number"
                }]
            },
            "value": request_doc.placer_order_number
        })
    
    # Add filler order number
    if request_doc.filler_order_number:
        procedure_request["identifier"].append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                    "code": "FILL",
                    "display": "Filler Order Number"
                }]
            },
            "value": request_doc.filler_order_number
        })
    
    # Add service code
    if request_doc.service_code or request_doc.service_name:
        procedure_request["code"] = {
            "coding": [{
                "code": request_doc.service_code or "",
                "display": request_doc.service_name or ""
            }]
        }
        if request_doc.service_name:
            procedure_request["code"]["text"] = request_doc.service_name
    
    # Add requested datetime
    if request_doc.requested_datetime:
        procedure_request["authoredOn"] = request_doc.requested_datetime.isoformat()
    
    # Add ordering provider
    if request_doc.ordering_provider:
        procedure_request["requester"] = {
            "display": request_doc.ordering_provider
        }
    
    # Add priority
    if request_doc.procedure_priority:
        priority_map = {
            "Routine": "routine",
            "Urgent": "urgent",
            "Stat": "stat",
            "Asap": "asap"
        }
        procedure_request["priority"] = priority_map.get(
            request_doc.procedure_priority,
            "routine"
        )
    
    # Add notes
    if request_doc.notes:
        procedure_request["note"] = [{
            "text": request_doc.notes
        }]
    
    # Add link to accession if present
    if request_doc.radiology_accession:
        procedure_request["basedOn"] = [{
            "reference": f"ImagingStudy/{request_doc.radiology_accession}"
        }]
    
    return procedure_request


def accession_to_fhir_imagingstudy(accession_doc):
    """
    Convert RadiologyAccession to FHIR ImagingStudy resource.
    
    Args:
        accession_doc: RadiologyAccession document or dict
    
    Returns:
        dict representing FHIR ImagingStudy resource
    """
    if isinstance(accession_doc, str):
        accession_doc = frappe.get_doc("Radiology Accession", accession_doc)
    
    # Build FHIR ImagingStudy resource
    imaging_study = {
        "resourceType": "ImagingStudy",
        "id": accession_doc.name,
        "identifier": [
            {
                "type": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                        "code": "ACSN",
                        "display": "Accession Number"
                    }]
                },
                "value": accession_doc.accession_number
            }
        ],
        "status": map_accession_status_to_fhir(accession_doc.status),
        "subject": {
            "reference": f"Patient/{accession_doc.patient}",
            "display": accession_doc.patient
        }
    }
    
    # Add Study Instance UID if available
    if accession_doc.study_instance_uid:
        imaging_study["identifier"].append({
            "system": "urn:dicom:uid",
            "value": f"urn:oid:{accession_doc.study_instance_uid}"
        })
    
    # Add study date and time
    if accession_doc.study_date:
        started = str(accession_doc.study_date)
        if accession_doc.study_time:
            started = f"{accession_doc.study_date}T{accession_doc.study_time}"
        imaging_study["started"] = started
    
    # Add modality
    if accession_doc.modality:
        imaging_study["modality"] = [{
            "system": "http://dicom.nema.org/resources/ontology/DCM",
            "code": accession_doc.modality
        }]
    
    # Add performing facility
    if accession_doc.performing_facility:
        imaging_study["location"] = {
            "display": accession_doc.performing_facility
        }
    
    # Add notes
    if accession_doc.notes:
        imaging_study["note"] = [{
            "text": accession_doc.notes
        }]
    
    # Add linked procedure requests
    if accession_doc.requests:
        imaging_study["basedOn"] = []
        for link in accession_doc.requests:
            imaging_study["basedOn"].append({
                "reference": f"ProcedureRequest/{link.procedure_request}",
                "display": link.service_name or link.external_request_id
            })
    
    # Set number of series and instances (default to 0 if not available)
    imaging_study["numberOfSeries"] = 0
    imaging_study["numberOfInstances"] = 0
    
    return imaging_study


def fhir_procedurerequest_to_request(fhir_resource):
    """
    Convert FHIR ProcedureRequest to RadiologyProcedureRequest data.
    
    Args:
        fhir_resource: FHIR ProcedureRequest resource dict
    
    Returns:
        dict with RadiologyProcedureRequest fields
    """
    request_data = {}
    
    # Extract patient
    if fhir_resource.get("subject"):
        subject_ref = fhir_resource["subject"].get("reference", "")
        if subject_ref.startswith("Patient/"):
            request_data["patient"] = subject_ref.replace("Patient/", "")
    
    # Extract identifiers
    identifiers = fhir_resource.get("identifier", [])
    for identifier in identifiers:
        id_type = identifier.get("type", {}).get("coding", [{}])[0].get("code", "")
        value = identifier.get("value", "")
        
        if id_type == "RPID":
            request_data["external_request_id"] = value
        elif id_type == "PLAC":
            request_data["placer_order_number"] = value
        elif id_type == "FILL":
            request_data["filler_order_number"] = value
    
    # If no RPID found, use first identifier
    if not request_data.get("external_request_id") and identifiers:
        request_data["external_request_id"] = identifiers[0].get("value", "")
    
    # Extract service code
    code = fhir_resource.get("code", {})
    if code:
        coding = code.get("coding", [{}])[0]
        request_data["service_code"] = coding.get("code", "")
        request_data["service_name"] = coding.get("display", "") or code.get("text", "")
    
    # Extract requested datetime
    if fhir_resource.get("authoredOn"):
        request_data["requested_datetime"] = fhir_resource["authoredOn"]
    
    # Extract priority
    if fhir_resource.get("priority"):
        priority_map = {
            "routine": "Routine",
            "urgent": "Urgent",
            "stat": "Stat",
            "asap": "Asap"
        }
        request_data["procedure_priority"] = priority_map.get(
            fhir_resource["priority"],
            "Routine"
        )
    
    # Extract notes
    if fhir_resource.get("note"):
        notes = [note.get("text", "") for note in fhir_resource["note"]]
        request_data["notes"] = "\n".join(notes)
    
    # Extract status
    if fhir_resource.get("status"):
        status_map = {
            "draft": "Pending",
            "active": "Scheduled",
            "in-progress": "In Progress",
            "completed": "Completed",
            "cancelled": "Cancelled"
        }
        request_data["status"] = status_map.get(
            fhir_resource["status"],
            "Pending"
        )
    
    return request_data


def fhir_imagingstudy_to_accession(fhir_resource):
    """
    Convert FHIR ImagingStudy to RadiologyAccession data.
    
    Args:
        fhir_resource: FHIR ImagingStudy resource dict
    
    Returns:
        dict with RadiologyAccession fields
    """
    accession_data = {}
    
    # Extract patient
    if fhir_resource.get("subject"):
        subject_ref = fhir_resource["subject"].get("reference", "")
        if subject_ref.startswith("Patient/"):
            accession_data["patient"] = subject_ref.replace("Patient/", "")
    
    # Extract identifiers
    identifiers = fhir_resource.get("identifier", [])
    for identifier in identifiers:
        id_type = identifier.get("type", {}).get("coding", [{}])[0].get("code", "")
        value = identifier.get("value", "")
        system = identifier.get("system", "")
        
        if id_type == "ACSN":
            accession_data["accession_number"] = value
        elif system == "urn:dicom:uid":
            # Extract UID from urn:oid: prefix
            uid = value.replace("urn:oid:", "")
            accession_data["study_instance_uid"] = uid
    
    # Extract study date/time
    if fhir_resource.get("started"):
        started = fhir_resource["started"]
        if "T" in started:
            date_part, time_part = started.split("T", 1)
            accession_data["study_date"] = date_part
            accession_data["study_time"] = time_part.split("+")[0].split("Z")[0]
        else:
            accession_data["study_date"] = started
    
    # Extract modality
    if fhir_resource.get("modality"):
        modalities = [m.get("code", "") for m in fhir_resource["modality"]]
        if modalities:
            accession_data["modality"] = modalities[0]
    
    # Extract location
    if fhir_resource.get("location"):
        accession_data["performing_facility"] = fhir_resource["location"].get("display", "")
    
    # Extract notes
    if fhir_resource.get("note"):
        notes = [note.get("text", "") for note in fhir_resource["note"]]
        accession_data["notes"] = "\n".join(notes)
    
    # Extract status
    if fhir_resource.get("status"):
        status_map = {
            "registered": "Scheduled",
            "available": "Completed",
            "cancelled": "Cancelled"
        }
        accession_data["status"] = status_map.get(
            fhir_resource["status"],
            "Scheduled"
        )
    
    return accession_data


def map_status_to_fhir(status):
    """Map RadiologyProcedureRequest status to FHIR ProcedureRequest status."""
    status_map = {
        "Pending": "draft",
        "Scheduled": "active",
        "In Progress": "in-progress",
        "Completed": "completed",
        "Cancelled": "cancelled"
    }
    return status_map.get(status, "draft")


def map_accession_status_to_fhir(status):
    """Map RadiologyAccession status to FHIR ImagingStudy status."""
    status_map = {
        "Scheduled": "registered",
        "Arrived": "registered",
        "In Progress": "registered",
        "Completed": "available",
        "Cancelled": "cancelled"
    }
    return status_map.get(status, "registered")
