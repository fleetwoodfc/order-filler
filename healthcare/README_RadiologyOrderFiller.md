# Radiology Order Filler Implementation

## Overview

This module implements a Radiology Order Filler actor compliant with IHE Radiology Technical Framework specifications, particularly focusing on the proper handling of Accession Numbers versus Requested Procedure IDs (RPID) as clarified in IHE Radiology TF Appendix A.

## Key Concepts

### Accession Number vs Requested Procedure ID (RPID)

Following IHE Radiology TF Appendix A:

- **Requested Procedure ID (RPID)**: A unique identifier for each imaging procedure request. Stored in `external_request_id` field. This comes from HL7 OBR-20 or is derived from the order.

- **Accession Number**: A unique identifier assigned by the imaging department that groups one or more procedure requests together into a single imaging study/encounter. This is what gets associated with DICOM images.

The relationship is many-to-one: multiple procedure requests (RPIDs) can be grouped under a single accession number for operational efficiency.

## Architecture

### DocTypes

#### 1. Radiology Procedure Request
Represents an individual procedure request from an external order system.

**Key Fields:**
- `external_request_id` (RPID): Unique identifier from source system (HL7 OBR-20)
- `placer_order_number`: Original order number from ordering system (HL7 ORC-2)
- `filler_order_number`: Order number assigned by filler system (HL7 ORC-3)
- `service_code`: Procedure code (e.g., RadLex code)
- `service_name`: Human-readable procedure name
- `patient`: Link to Patient DocType
- `radiology_accession`: Link to Radiology Accession
- `status`: Current status (Pending, Scheduled, In Progress, Completed, Cancelled)

#### 2. Radiology Accession
Represents an imaging study with an assigned accession number.

**Key Fields:**
- `accession_number`: Unique accession number (auto-generated or from message)
- `patient`: Link to Patient DocType
- `study_instance_uid`: DICOM Study Instance UID (when available)
- `study_date` / `study_time`: When the study is/was performed
- `modality`: Imaging modality (CT, MR, XR, etc.)
- `requests`: Table of linked Radiology Procedure Requests
- `status`: Current status (Scheduled, Arrived, In Progress, Completed, Cancelled)

#### 3. Radiology Accession Request Link
Child table linking accessions to procedure requests (many-to-many relationship).

## Configuration

Configuration is done via `site_config.json` or through Frappe's configuration system.

### Available Configuration Options

```json
{
  "radiology": {
    "auto_generate_accession": true,
    "facility_code": "RAD",
    "accession_pattern": "{facility_code}-{YYYYMMDD}-{seq:06d}"
  }
}
```

**Configuration Keys:**

- `auto_generate_accession` (boolean, default: `true`): Whether to automatically generate accession numbers for incoming requests that don't have one
- `facility_code` (string, default: `"RAD"`): Facility identifier used in accession number pattern
- `accession_pattern` (string): Pattern for generating accession numbers

### Accession Number Pattern

The pattern supports these placeholders:
- `{facility_code}`: Configured facility code
- `{YYYY}`: 4-digit year
- `{YY}`: 2-digit year
- `{MM}`: 2-digit month
- `{DD}`: 2-digit day
- `{YYYYMMDD}`: Combined date (20251110)
- `{seq:06d}`: Sequence number with padding (000001, 000002, etc.)

**Examples:**
- `{facility_code}-{YYYYMMDD}-{seq:06d}` → `RAD-20251110-000001`
- `ACC{YY}{MM}{DD}{seq:04d}` → `ACC2511100001`
- `{facility_code}.{YYYY}.{seq:08d}` → `RAD.2025.00000001`

Sequence numbers are reset daily based on the date component in the pattern.

## Workflows

### Workflow 1: Receiving HL7 ORM Order with Accession Number

```
External System → HL7 ORM^O01 (with OBR-18 accession) → receive_hl7 endpoint
                                                           ↓
                                              RadiologyProcedureRequest created
                                                           ↓
                                              RadiologyAccession created/linked
                                                           ↓
                                              Response with accession number
```

**HL7 Message Example:**
```
MSH|^~\&|PLACER|HOSPITAL|FILLER|RADIOLOGY|20251110120000||ORM^O01|123456|P|2.5
PID|1||PAT001||Doe^John||19800101|M
ORC|NW|ORDER123|||||^^^20251110120000
OBR|1|ORDER123||CT^CT Chest^RADLEX|||20251110120000||||||||||||ACC20251110001||||||
```

In this case:
- RPID = "CT" (from OBR-4 or OBR-20)
- Accession Number = "ACC20251110001" (from OBR-18)
- System creates RadiologyProcedureRequest with RPID
- System creates RadiologyAccession with provided accession number
- Links them together

### Workflow 2: Receiving HL7 ORM Order without Accession Number

```
External System → HL7 ORM^O01 (no OBR-18) → receive_hl7 endpoint
                                                ↓
                                   RadiologyProcedureRequest created
                                                ↓
                            Auto-generate accession (if enabled)
                                                ↓
                               RadiologyAccession created
                                                ↓
                        Response with generated accession number
```

**HL7 Message Example:**
```
MSH|^~\&|PLACER|HOSPITAL|FILLER|RADIOLOGY|20251110120000||ORM^O01|123457|P|2.5
PID|1||PAT001||Doe^John||19800101|M
ORC|NW|ORDER124|||||^^^20251110120000
OBR|1|ORDER124||MR^MRI Brain^RADLEX|||20251110120000
```

In this case:
- RPID = "MR" or derived from ORDER124
- No accession number in message
- System creates RadiologyProcedureRequest with RPID
- System auto-generates accession number (e.g., "RAD-20251110-000001")
- System creates RadiologyAccession with generated number
- Returns generated accession in response

### Workflow 3: Multiple Requests for Same Accession

```
Request 1 → receive_hl7 → Create Request + Accession (ACC001)
                                     ↓
Request 2 (same patient, same accession) → receive_hl7 → Create Request + Link to ACC001
                                     ↓
RadiologyAccession ACC001 now links to both requests
```

## Integration Points

### 1. HL7 v2 Endpoint

**Endpoint:** `/api/method/healthcare.integrations.hl7.receive_hl7.receive_hl7`

**Method:** POST (whitelisted, requires authentication)

**Parameters:**
- `message` (string): HL7 v2 message in pipe-delimited format
- `message_type` (string, optional): Message type hint (e.g., "ORM^O01")

**Response:**
```json
{
  "status": "success",
  "message": "Procedure request created successfully",
  "patient": "PAT-001",
  "request_id": "hash123",
  "external_request_id": "RPID-001",
  "accession_number": "RAD-20251110-000001",
  "accession_generated": true
}
```

**Error Response:**
```json
{
  "status": "error",
  "message": "Failed to process HL7 message",
  "error": "Error details"
}
```

### 2. FHIR API

The module provides mapping functions for FHIR R4 resources:

**Functions:**
- `request_to_fhir_procedurerequest(request_doc)`: Convert to FHIR ProcedureRequest
- `accession_to_fhir_imagingstudy(accession_doc)`: Convert to FHIR ImagingStudy
- `fhir_procedurerequest_to_request(fhir_resource)`: Parse FHIR ProcedureRequest
- `fhir_imagingstudy_to_accession(fhir_resource)`: Parse FHIR ImagingStudy

**Example Usage:**
```python
from healthcare.integrations.fhir.fhir_mapper import (
    request_to_fhir_procedurerequest,
    accession_to_fhir_imagingstudy
)

# Convert DocType to FHIR
request = frappe.get_doc("Radiology Procedure Request", "REQ-001")
fhir_request = request_to_fhir_procedurerequest(request)

accession = frappe.get_doc("Radiology Accession", "ACC-001")
fhir_study = accession_to_fhir_imagingstudy(accession)
```

### 3. DICOM Integration

The `study_instance_uid` field in RadiologyAccession should be populated when DICOM images are created:

```python
accession = frappe.get_doc("Radiology Accession", accession_number)
accession.study_instance_uid = "1.2.840.10008.5.1.4.1.1.1.1"
accession.save()
```

This UID should match the DICOM Study Instance UID (0020,000D) in all images for this study.

## Running the HL7 Endpoint

### Option 1: Via Frappe API

Call the whitelisted endpoint using Frappe's API:

```bash
curl -X POST \
  -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "MSH|^~\\&|PLACER|...",
    "message_type": "ORM^O01"
  }' \
  http://your-site.com/api/method/healthcare.integrations.hl7.receive_hl7.receive_hl7
```

### Option 2: MLLP Listener (Existing)

The existing MLLP listener in `healthcare/ris/hl7_listener.py` can be configured to forward to this endpoint:

```python
# In your processor
from healthcare.integrations.hl7.receive_hl7 import receive_hl7

result = receive_hl7(hl7_message, message_type)
```

### Option 3: Direct Integration

Import and call the function directly:

```python
from healthcare.integrations.hl7.receive_hl7 import receive_hl7

hl7_message = """MSH|^~\\&|PLACER|HOSPITAL|FILLER|RADIOLOGY|..."""

result = receive_hl7(message=hl7_message, message_type="ORM^O01")

if result["status"] == "success":
    print(f"Accession: {result['accession_number']}")
```

## Testing

Unit tests are provided in `healthcare/tests/test_radiology_order_filler.py`.

### Running Tests

```bash
# Run all healthcare tests
bench --site your-site.com run-tests --app healthcare

# Run specific test module
bench --site your-site.com run-tests --module healthcare.tests.test_radiology_order_filler
```

### Test Coverage

Tests cover:
- Accession number generation with uniqueness verification
- HL7 ORM message parsing and DocType creation
- Linking requests to accessions
- Handling duplicate accession numbers
- FHIR mapping functions

## Security Considerations

1. **Authentication**: The HL7 endpoint requires authentication. Use API keys or tokens.

2. **Patient Matching**: The system attempts to match patients by identifier. Ensure patient identifiers are properly configured.

3. **Data Validation**: All incoming data is validated before creating DocTypes.

4. **Audit Trail**: All HL7 messages are logged in the HL7 Message Log DocType with status tracking.

5. **Permissions**: DocType permissions should be configured appropriately:
   - System Manager: Full access
   - Healthcare Administrator: Create, read, write
   - Radiologist: Read, update specific fields

## Troubleshooting

### Accession Numbers Not Generating

Check configuration:
```python
import frappe
config = frappe.get_site_config()
print(config.get("radiology", {}))
```

Verify auto-generation is enabled:
```json
{
  "radiology": {
    "auto_generate_accession": true
  }
}
```

### HL7 Message Processing Failures

Check HL7 Message Log DocType for error details:

```python
logs = frappe.get_all("HL7 Message Log", 
    filters={"status": "Failed"}, 
    fields=["name", "message_type", "error"],
    limit=10)
```

### Patient Not Found

Ensure patients exist in the system and have proper identifiers configured. The system looks for:
1. Patient identifier (PID-3)
2. Name + DOB match (PID-5 + PID-7)

### Duplicate Accession Numbers

The system includes uniqueness checks. If duplicates occur:
1. Check for race conditions in high-volume environments
2. Verify database constraints are in place
3. Review accession pattern configuration

## Future Enhancements

- Support for additional HL7 message types (ORU, SIU)
- Enhanced DICOM worklist integration
- Real-time status updates via FHIR subscriptions
- Advanced scheduling features
- Integration with reporting systems
- Support for procedure modifiers and contrast indicators

## References

- IHE Radiology Technical Framework: https://www.ihe.net/resources/technical_frameworks/#radiology
- HL7 v2.5 Specification: https://www.hl7.org/implement/standards/product_brief.cfm?product_id=144
- FHIR R4 Specification: http://hl7.org/fhir/R4/
- DICOM Standard: https://www.dicomstandard.org/

## Support

For issues or questions:
1. Check the HL7 Message Log for error details
2. Review Frappe error logs: `bench --site your-site.com console`
3. Enable debug logging for detailed traces

## License

This module is part of Marley Health and is licensed under GNU GPL V3.
