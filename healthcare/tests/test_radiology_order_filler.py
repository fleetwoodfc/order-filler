"""
Unit Tests for Radiology Order Filler

Tests for:
- Accession number generation and uniqueness
- HL7 message parsing and DocType creation
- FHIR mapping functions
- Request-to-accession linking
"""

import frappe
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestRadiologyOrderFiller(unittest.TestCase):
    """Test suite for Radiology Order Filler functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        frappe.set_user("Administrator")
    
    def setUp(self):
        """Set up for each test."""
        # Clean up any existing test data
        self.cleanup_test_data()
    
    def tearDown(self):
        """Clean up after each test."""
        self.cleanup_test_data()
    
    def cleanup_test_data(self):
        """Remove test data from database."""
        test_rpids = ["TEST-RPID-001", "TEST-RPID-002", "TEST-RPID-003"]
        for rpid in test_rpids:
            if frappe.db.exists("Radiology Procedure Request", {"external_request_id": rpid}):
                requests = frappe.get_all(
                    "Radiology Procedure Request",
                    filters={"external_request_id": rpid},
                    pluck="name"
                )
                for req in requests:
                    frappe.delete_doc("Radiology Procedure Request", req, force=True)
        
        # Clean up test accessions
        test_accessions = ["TEST-ACC-001", "TEST-ACC-002"]
        for acc in test_accessions:
            if frappe.db.exists("Radiology Accession", acc):
                frappe.delete_doc("Radiology Accession", acc, force=True)
        
        frappe.db.commit()
    
    def test_accession_number_generation(self):
        """Test accession number generation with default pattern."""
        from healthcare.doctype.radiology_procedure_request.radiology_procedure_request import (
            generate_accession_number
        )
        
        # Generate first accession
        accession1 = generate_accession_number()
        self.assertIsNotNone(accession1)
        self.assertIsInstance(accession1, str)
        self.assertTrue(len(accession1) > 0)
        
        # Generate second accession - should be different
        accession2 = generate_accession_number()
        self.assertIsNotNone(accession2)
        self.assertNotEqual(accession1, accession2)
        
        # Verify pattern (default: {facility_code}-{YYYYMMDD}-{seq:06d})
        # Should match pattern like RAD-20251110-000001
        parts = accession1.split("-")
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].isalpha())  # Facility code
        self.assertTrue(parts[1].isdigit() and len(parts[1]) == 8)  # Date YYYYMMDD
        self.assertTrue(parts[2].isdigit() and len(parts[2]) == 6)  # Sequence
    
    def test_accession_uniqueness(self):
        """Test that generated accession numbers are unique."""
        from healthcare.doctype.radiology_procedure_request.radiology_procedure_request import (
            generate_accession_number
        )
        
        # Generate multiple accessions and verify uniqueness
        accessions = set()
        for i in range(10):
            acc = generate_accession_number()
            self.assertNotIn(acc, accessions, f"Duplicate accession generated: {acc}")
            accessions.add(acc)
        
        self.assertEqual(len(accessions), 10)
    
    def test_create_procedure_request(self):
        """Test creating a RadiologyProcedureRequest."""
        # Create a test patient if not exists
        if not frappe.db.exists("Patient", "TEST-PAT-001"):
            patient = frappe.new_doc("Patient")
            patient.first_name = "Test"
            patient.last_name = "Patient"
            patient.patient_identifier = "TEST-PAT-001"
            patient.insert(ignore_permissions=True)
        
        # Create procedure request
        request = frappe.new_doc("Radiology Procedure Request")
        request.patient = "TEST-PAT-001"
        request.external_request_id = "TEST-RPID-001"
        request.placer_order_number = "ORDER-001"
        request.service_code = "CT"
        request.service_name = "CT Chest"
        request.status = "Pending"
        
        request.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Verify it was created
        self.assertTrue(frappe.db.exists("Radiology Procedure Request", request.name))
        
        # Verify fields
        saved_request = frappe.get_doc("Radiology Procedure Request", request.name)
        self.assertEqual(saved_request.external_request_id, "TEST-RPID-001")
        self.assertEqual(saved_request.service_name, "CT Chest")
    
    def test_duplicate_rpid_validation(self):
        """Test that duplicate external_request_id is prevented."""
        # Create a test patient
        if not frappe.db.exists("Patient", "TEST-PAT-001"):
            patient = frappe.new_doc("Patient")
            patient.first_name = "Test"
            patient.last_name = "Patient"
            patient.patient_identifier = "TEST-PAT-001"
            patient.insert(ignore_permissions=True)
        
        # Create first request
        request1 = frappe.new_doc("Radiology Procedure Request")
        request1.patient = "TEST-PAT-001"
        request1.external_request_id = "TEST-RPID-002"
        request1.service_name = "MRI Brain"
        request1.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Try to create duplicate
        request2 = frappe.new_doc("Radiology Procedure Request")
        request2.patient = "TEST-PAT-001"
        request2.external_request_id = "TEST-RPID-002"  # Same RPID
        request2.service_name = "CT Chest"
        
        with self.assertRaises(frappe.exceptions.ValidationError):
            request2.insert(ignore_permissions=True)
    
    def test_link_request_to_accession(self):
        """Test linking a procedure request to an accession."""
        from healthcare.doctype.radiology_procedure_request.radiology_procedure_request import (
            link_request_to_accession
        )
        
        # Create test patient
        if not frappe.db.exists("Patient", "TEST-PAT-001"):
            patient = frappe.new_doc("Patient")
            patient.first_name = "Test"
            patient.last_name = "Patient"
            patient.patient_identifier = "TEST-PAT-001"
            patient.insert(ignore_permissions=True)
        
        # Create procedure request
        request = frappe.new_doc("Radiology Procedure Request")
        request.patient = "TEST-PAT-001"
        request.external_request_id = "TEST-RPID-003"
        request.service_name = "XR Chest"
        request.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Create accession
        accession = frappe.new_doc("Radiology Accession")
        accession.accession_number = "TEST-ACC-001"
        accession.patient = "TEST-PAT-001"
        accession.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Link them
        result = link_request_to_accession(request.name, accession.name)
        
        self.assertEqual(result["request"], request.name)
        self.assertEqual(result["accession"], accession.name)
        self.assertEqual(result["accession_number"], "TEST-ACC-001")
        
        # Verify link
        saved_request = frappe.get_doc("Radiology Procedure Request", request.name)
        self.assertEqual(saved_request.radiology_accession, accession.name)
        
        saved_accession = frappe.get_doc("Radiology Accession", accession.name)
        self.assertEqual(len(saved_accession.requests), 1)
        self.assertEqual(saved_accession.requests[0].procedure_request, request.name)
    
    def test_hl7_message_parsing(self):
        """Test parsing HL7 ORM message and extracting order info."""
        from healthcare.integrations.hl7.receive_hl7 import extract_order_info
        
        try:
            from hl7apy.parser import parse_message
        except ImportError:
            self.skipTest("hl7apy not installed")
        
        # Sample HL7 ORM message
        hl7_message = (
            "MSH|^~\\&|PLACER|HOSPITAL|FILLER|RADIOLOGY|20251110120000||ORM^O01|123456|P|2.5\r"
            "PID|1||PAT001||Doe^John||19800101|M\r"
            "ORC|NW|ORDER123|FILLER123||||^^^20251110120000\r"
            "OBR|1|ORDER123|FILLER123|CT^CT Chest^RADLEX|R||20251110120000||||||||||||ACC001||||||"
        )
        
        parsed = parse_message(hl7_message)
        order_info = extract_order_info(parsed)
        
        self.assertEqual(order_info["placer_order_number"], "ORDER123")
        self.assertEqual(order_info["filler_order_number"], "FILLER123")
        self.assertEqual(order_info["service_code"], "CT")
        self.assertEqual(order_info["service_name"], "CT Chest")
        self.assertEqual(order_info["accession_number"], "ACC001")
        self.assertEqual(order_info["priority"], "Routine")
    
    def test_fhir_procedurerequest_mapping(self):
        """Test mapping RadiologyProcedureRequest to FHIR ProcedureRequest."""
        from healthcare.integrations.fhir.fhir_mapper import request_to_fhir_procedurerequest
        
        # Create test patient
        if not frappe.db.exists("Patient", "TEST-PAT-001"):
            patient = frappe.new_doc("Patient")
            patient.first_name = "Test"
            patient.last_name = "Patient"
            patient.patient_identifier = "TEST-PAT-001"
            patient.insert(ignore_permissions=True)
        
        # Create procedure request
        request = frappe.new_doc("Radiology Procedure Request")
        request.patient = "TEST-PAT-001"
        request.external_request_id = "RPID-123"
        request.placer_order_number = "PLACER-123"
        request.service_code = "MR"
        request.service_name = "MRI Brain"
        request.status = "Pending"
        request.procedure_priority = "Urgent"
        request.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Map to FHIR
        fhir_resource = request_to_fhir_procedurerequest(request)
        
        self.assertEqual(fhir_resource["resourceType"], "ProcedureRequest")
        self.assertEqual(fhir_resource["status"], "draft")
        self.assertEqual(fhir_resource["priority"], "urgent")
        self.assertEqual(fhir_resource["subject"]["reference"], "Patient/TEST-PAT-001")
        
        # Check identifiers
        identifiers = {
            id["type"]["coding"][0]["code"]: id["value"]
            for id in fhir_resource["identifier"]
            if "type" in id
        }
        self.assertEqual(identifiers["RPID"], "RPID-123")
        self.assertEqual(identifiers["PLAC"], "PLACER-123")
        
        # Check code
        self.assertEqual(fhir_resource["code"]["coding"][0]["code"], "MR")
        self.assertEqual(fhir_resource["code"]["coding"][0]["display"], "MRI Brain")
    
    def test_fhir_imagingstudy_mapping(self):
        """Test mapping RadiologyAccession to FHIR ImagingStudy."""
        from healthcare.integrations.fhir.fhir_mapper import accession_to_fhir_imagingstudy
        
        # Create test patient
        if not frappe.db.exists("Patient", "TEST-PAT-001"):
            patient = frappe.new_doc("Patient")
            patient.first_name = "Test"
            patient.last_name = "Patient"
            patient.patient_identifier = "TEST-PAT-001"
            patient.insert(ignore_permissions=True)
        
        # Create accession
        accession = frappe.new_doc("Radiology Accession")
        accession.accession_number = "TEST-ACC-002"
        accession.patient = "TEST-PAT-001"
        accession.study_instance_uid = "1.2.840.10008.5.1.4.1.1.1.1"
        accession.status = "Completed"
        accession.modality = "CT"
        accession.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Map to FHIR
        fhir_resource = accession_to_fhir_imagingstudy(accession)
        
        self.assertEqual(fhir_resource["resourceType"], "ImagingStudy")
        self.assertEqual(fhir_resource["status"], "available")
        self.assertEqual(fhir_resource["subject"]["reference"], "Patient/TEST-PAT-001")
        
        # Check identifiers
        identifiers = {
            id.get("type", {}).get("coding", [{}])[0].get("code", id.get("system", "")): id["value"]
            for id in fhir_resource["identifier"]
        }
        self.assertEqual(identifiers["ACSN"], "TEST-ACC-002")
        self.assertIn("1.2.840.10008.5.1.4.1.1.1.1", identifiers["urn:dicom:uid"])
        
        # Check modality
        self.assertEqual(fhir_resource["modality"][0]["code"], "CT")


def run_tests():
    """Run the test suite."""
    unittest.main()


if __name__ == "__main__":
    run_tests()
