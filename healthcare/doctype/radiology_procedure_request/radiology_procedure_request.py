"""
Radiology Procedure Request DocType Controller

This module implements the server-side controller for Radiology Procedure Request,
including accession number generation following IHE Radiology TF specifications.

Key responsibilities:
- Validate external_request_id (RPID) uniqueness
- Generate accession numbers when auto-assignment is enabled
- Link requests to accessions
- Implement configurable accession number patterns
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now, today, nowdate
import re
from datetime import datetime


class RadiologyProcedureRequest(Document):
    """
    Represents a radiology procedure request from an external system.
    
    Stores the Requested Procedure ID (RPID) as external_request_id and can be
    linked to a Radiology Accession which groups one or more requests together.
    """
    
    def validate(self):
        """Validate the procedure request before save."""
        # Ensure external_request_id is present and unique
        if not self.external_request_id:
            frappe.throw("External Request ID (RPID) is required")
        
        # Check for duplicates (excluding current doc if updating)
        existing = frappe.db.exists(
            "Radiology Procedure Request",
            {
                "external_request_id": self.external_request_id,
                "name": ["!=", self.name]
            }
        )
        if existing:
            frappe.throw(f"A procedure request with RPID {self.external_request_id} already exists")
    
    def before_insert(self):
        """Hook called before inserting a new document."""
        # Auto-generate accession if enabled and not already set
        if not self.radiology_accession:
            if should_auto_generate_accession():
                try:
                    accession = create_accession_for_request(self)
                    self.radiology_accession = accession.name
                except Exception as e:
                    frappe.log_error(
                        message=str(e),
                        title="Failed to auto-generate accession"
                    )


def should_auto_generate_accession():
    """
    Check if auto-generation of accession numbers is enabled.
    
    Configuration via frappe.conf:
    - radiology.auto_generate_accession (default: True)
    """
    site_config = frappe.get_site_config() or {}
    radiology_config = site_config.get("radiology", {})
    return radiology_config.get("auto_generate_accession", True)


def get_accession_pattern():
    """
    Get the configured accession number pattern.
    
    Pattern can include:
    - {facility_code}: facility identifier
    - {YYYY}: 4-digit year
    - {YY}: 2-digit year
    - {MM}: 2-digit month
    - {DD}: 2-digit day
    - {seq:06d}: sequence number with padding
    
    Default pattern: {facility_code}-{YYYYMMDD}-{seq:06d}
    Example: RAD-20251110-000001
    """
    site_config = frappe.get_site_config() or {}
    radiology_config = site_config.get("radiology", {})
    return radiology_config.get("accession_pattern", "{facility_code}-{YYYYMMDD}-{seq:06d}")


def get_facility_code():
    """
    Get the facility code from configuration.
    
    Default: RAD
    """
    site_config = frappe.get_site_config() or {}
    radiology_config = site_config.get("radiology", {})
    return radiology_config.get("facility_code", "RAD")


def generate_accession_number():
    """
    Generate a unique accession number using the configured pattern.
    
    Uses Frappe's Counter DocType for atomic sequence generation.
    Returns a unique accession number string.
    """
    pattern = get_accession_pattern()
    facility_code = get_facility_code()
    
    # Get current date components
    now_dt = datetime.now()
    year_4 = now_dt.strftime("%Y")
    year_2 = now_dt.strftime("%y")
    month = now_dt.strftime("%m")
    day = now_dt.strftime("%d")
    
    # Build the date part for counter key
    date_part = f"{year_4}{month}{day}"
    counter_key = f"radiology_accession_{date_part}"
    
    # Get next sequence number (atomic operation)
    # frappe.db.get_next_sequence_val creates a counter if not exists
    sequence = frappe.db.get_next_sequence_val(counter_key)
    
    # Format the accession number
    accession = pattern.format(
        facility_code=facility_code,
        YYYY=year_4,
        YY=year_2,
        MM=month,
        DD=day,
        YYYYMMDD=date_part,
        seq=sequence
    )
    
    # Ensure uniqueness (paranoid check)
    max_attempts = 100
    attempts = 0
    while frappe.db.exists("Radiology Accession", accession) and attempts < max_attempts:
        attempts += 1
        sequence = frappe.db.get_next_sequence_val(counter_key)
        accession = pattern.format(
            facility_code=facility_code,
            YYYY=year_4,
            YY=year_2,
            MM=month,
            DD=day,
            YYYYMMDD=date_part,
            seq=sequence
        )
    
    if attempts >= max_attempts:
        frappe.throw("Unable to generate unique accession number after maximum attempts")
    
    return accession


def create_accession_for_request(procedure_request):
    """
    Create a new Radiology Accession for a procedure request.
    
    Args:
        procedure_request: RadiologyProcedureRequest document (can be unsaved)
    
    Returns:
        RadiologyAccession document
    """
    accession_number = generate_accession_number()
    
    accession = frappe.new_doc("Radiology Accession")
    accession.accession_number = accession_number
    accession.patient = procedure_request.patient
    accession.study_date = nowdate()
    accession.status = "Scheduled"
    
    # Don't add to requests table yet if procedure_request is not saved
    # The link will be established after both are saved
    
    accession.insert(ignore_permissions=True)
    frappe.db.commit()
    
    return accession


@frappe.whitelist()
def link_request_to_accession(request_name, accession_name):
    """
    Link an existing procedure request to an existing accession.
    
    Args:
        request_name: Name of the Radiology Procedure Request
        accession_name: Name of the Radiology Accession
    """
    request = frappe.get_doc("Radiology Procedure Request", request_name)
    accession = frappe.get_doc("Radiology Accession", accession_name)
    
    # Verify patient matches
    if request.patient != accession.patient:
        frappe.throw("Patient mismatch between request and accession")
    
    # Update request
    request.radiology_accession = accession_name
    request.save(ignore_permissions=True)
    
    # Add to accession's request table if not already present
    existing_link = None
    for link in accession.requests:
        if link.procedure_request == request_name:
            existing_link = link
            break
    
    if not existing_link:
        accession.append("requests", {
            "procedure_request": request_name,
            "external_request_id": request.external_request_id,
            "service_name": request.service_name
        })
        accession.save(ignore_permissions=True)
    
    frappe.db.commit()
    
    return {
        "request": request_name,
        "accession": accession_name,
        "accession_number": accession.accession_number
    }


@frappe.whitelist()
def get_or_create_accession(request_name, accession_number=None):
    """
    Get an existing accession by number or create a new one for a request.
    
    Args:
        request_name: Name of the Radiology Procedure Request
        accession_number: Optional accession number to link to
    
    Returns:
        dict with accession details
    """
    request = frappe.get_doc("Radiology Procedure Request", request_name)
    
    if accession_number:
        # Try to find existing accession
        if frappe.db.exists("Radiology Accession", accession_number):
            accession = frappe.get_doc("Radiology Accession", accession_number)
            
            # Verify patient matches
            if request.patient != accession.patient:
                frappe.throw("Patient mismatch between request and existing accession")
            
            # Link them
            return link_request_to_accession(request_name, accession.name)
        else:
            # Create accession with specified number
            accession = frappe.new_doc("Radiology Accession")
            accession.accession_number = accession_number
            accession.patient = request.patient
            accession.study_date = nowdate()
            accession.status = "Scheduled"
            accession.insert(ignore_permissions=True)
            frappe.db.commit()
            
            return link_request_to_accession(request_name, accession.name)
    else:
        # Generate new accession
        accession = create_accession_for_request(request)
        return link_request_to_accession(request_name, accession.name)
