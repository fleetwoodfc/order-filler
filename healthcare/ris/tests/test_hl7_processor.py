# -*- coding: utf-8 -*-
# Integration test for HL7 ORM -> Service Request
import frappe
from frappe.tests import IntegrationTestCase
from hl7apy.parser import parse_message

from healthcare.ris import processor


class TestHL7Processor(IntegrationTestCase):
    def setUp(self):
        # create a patient with identifier '12345' to be matched by PID-3
        if not frappe.db.exists("Patient", {"patient_identifier": "12345"}):
            p = frappe.get_doc({
                "doctype": "Patient",
                "patient_name": "John Doe",
                "patient_identifier": "12345"
            })
            p.insert(ignore_permissions=True)
            self.patient = p
        else:
            name = frappe.db.get_value("Patient", {"patient_identifier": "12345"})
            self.patient = frappe.get_doc("Patient", name)

    def tearDown(self):
        # remove service requests created by test
        srs = frappe.get_all("Service Request", filters={"patient": self.patient.name})
        for s in srs:
            try:
                doc = frappe.get_doc("Service Request", s.name)
                if doc.docstatus == 1:
                    doc.cancel()
                doc.delete()
            except Exception:
                pass
        # leave patient as-is (optional deletion omitted)

    def test_process_orm_creates_service_request(self):
        hl7 = (
            "MSH|^~\\&|SENDER|SENDER_FAC|RECEIVER|RECEIVER_FAC|20251105||ORM^O01|MSGTEST1|P|2.3\r"
            "PID|1||12345^^^MRN||Doe^John||19800101|M|||123 Main St^^Metropolis^NY^10001||555-0100\r"
            "ORC|NW|ORD0001||||\r"
            "OBR|1|ORD0001||CBC^Complete Blood Count^L|||20251105\r"
        )
        msg = parse_message(hl7, validation_level=0)
        success = processor.process_hl7_message(msg, raw_message_text=hl7)
        self.assertTrue(success)
        # check a Service Request exists for this patient with template name "Complete Blood Count"
        sr_exists = frappe.db.exists("Service Request", {"patient": self.patient.name, "template_dn": "Complete Blood Count"})
        self.assertTrue(sr_exists)
