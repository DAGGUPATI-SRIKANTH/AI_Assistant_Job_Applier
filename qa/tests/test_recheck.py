"""Re-check: the tailored resume scored again from its own files, and the breaks a PDF copy leaves."""

from docx import Document

from ml.resume import recheck


def _docx(path, lines):
    doc = Document()
    for style, text in lines:
        doc.add_paragraph(text, style=style)
    doc.save(path)
    return path


def test_text_copied_out_of_a_pdf_is_caught_and_clean_text_is_not(tmp_path):
    broken = _docx(tmp_path / "broken.docx", [
        ("Normal", "Jordan Example"), ("Normal", "jordan@example.com"), ("Heading 1", "SKILLS"),
        ("Normal", "Cloud Platforms: AWS (EC2, S3), GCP (GKE, Cloud Bui ld)"),
        ("Normal", "Puppet, Bash, Groovy, YAML, JSON:"),
        ("Normal", "Built multi -cloud pipelines with SonarQ ube gates.")])
    found = " | ".join(recheck.text_problems(broken))
    assert "Bui ld" in found and "SonarQ ube" in found and "multi -cloud" in found and "JSON:" in found
    clean = _docx(tmp_path / "clean.docx", [
        ("Normal", "Jordan Example"), ("Normal", "jordan@example.com"), ("Heading 1", "SKILLS"),
        ("Normal", "Cloud Platforms: AWS (EC2, S3), GCP (GKE, Cloud Run, Cloud Build)"),
        ("Normal", "Built multi-cloud pipelines with SonarQube gates as a team of four.")])
    assert recheck.text_problems(clean) == []


def test_the_planned_lines_come_from_the_docx(tmp_path):
    path = _docx(tmp_path / "r.docx", [
        ("Normal", "Jordan Example"), ("Normal", "Denver, CO · jordan@example.com"), ("Heading 1", "EXPERIENCE"),
        ("Normal", "PLATFORM ENGINEER, ACME, USA\tJan 2022 – Present"), ("Normal", "• Ran Kubernetes clusters on EKS.")])
    assert [k for k, _ in recheck.blocks_of(path)] == ["name", "contact", "heading", "role", "bullet"]
