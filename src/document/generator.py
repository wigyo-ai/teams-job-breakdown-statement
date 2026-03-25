"""
Document Generator
Renders approved JBS JSON to a corporate Word (.docx) file,
uploads to S3-compatible storage, and returns a presigned download URL.
"""

import os
import json
import uuid
import boto3
from docx import Document
from docx.shared import Pt, RGBColor

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "../../templates/jbs_corporate_template.docx")
S3_BUCKET  = os.environ.get("S3_BUCKET", "certis-jbs-documents")
S3_PREFIX  = os.environ.get("S3_PREFIX", "jbs-documents/")
URL_EXPIRY = int(os.environ.get("DOC_URL_EXPIRY_SECONDS", "900"))


class DocumentGenerator:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

    async def generate(self, jbs_json: dict) -> str:
        doc = Document(TEMPLATE_PATH)
        self._populate_document(doc, jbs_json)

        site = jbs_json["metadata"]["site_name"].replace(" ", "_")
        filename = f"JBS_{site}_{uuid.uuid4().hex[:8]}.docx"
        local_path = f"/tmp/{filename}"
        doc.save(local_path)

        s3_key = f"{S3_PREFIX}{filename}"
        self.s3.upload_file(local_path, S3_BUCKET, s3_key)

        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=URL_EXPIRY,
        )

    def _populate_document(self, doc: Document, data: dict):
        meta = data["metadata"]
        bookmarks = {
            "{CUSTOMER_NAME}": meta.get("customer_name", ""),
            "{SITE_NAME}":     meta.get("site_name", ""),
            "{SITE_CATEGORY}": meta.get("site_category", ""),
            "{JOB_PURPOSE}":   meta.get("job_purpose", ""),
            "{GENERATED_AT}":  data.get("generated_at", ""),
            "{AUTHORIZED_BY}": meta.get("authorized_by", ""),
        }
        for para in doc.paragraphs:
            for placeholder, value in bookmarks.items():
                if placeholder in para.text:
                    para.text = para.text.replace(placeholder, value)

        # Duties tables
        for duty in data.get("duties", []):
            doc.add_heading(duty["duty_name"], level=2)
            table = doc.add_table(rows=1, cols=5)
            table.style = "Table Grid"
            hdr = table.rows[0].cells
            for i, h in enumerate(["#", "Task", "Trigger", "Frequency", "Role"]):
                hdr[i].text = h
            for task in duty.get("tasks", []):
                row = table.add_row().cells
                row[0].text = str(task.get("sequence", ""))
                row[1].text = task.get("task_description", "")
                row[2].text = task.get("trigger", "")
                row[3].text = task.get("frequency", "")
                row[4].text = task.get("responsible_role", "")

        # Safety section
        sc = data.get("safety_compliance", {})
        doc.add_heading("Safety & Compliance", level=1)
        doc.add_paragraph(f"Hazards: {', '.join(sc.get('site_hazards', []))}")
        doc.add_paragraph(f"PPE: {', '.join(sc.get('ppe_requirements', []))}")

        # Append machine-readable JSON as hidden audit trail
        doc.add_page_break()
        doc.add_heading("Appendix: Machine-Readable Data", level=1)
        p = doc.add_paragraph()
        run = p.add_run(f"<JBS_DATA>{json.dumps(data)}</JBS_DATA>")
        run.font.size = Pt(6)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
