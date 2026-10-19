"""The files an employer receives carry the person's name, what they are, and the company."""

from backend.apply.profile import Applicant
from backend.apply.runner import file_name, sent_copy


def test_the_name_says_who_what_and_for_whom(tmp_path):
    person = Applicant(personal={"first_name": "Jordan Lee", "last_name": "Example"})
    assert file_name(person, "Initech", "Resume", ".pdf") == "Jordan_Lee_Example_Resume_Initech.pdf"
    assert file_name(person, "Hooli AI", "Cover_Letter", ".pdf") == "Jordan_Lee_Example_Cover_Letter_Hooli_AI.pdf"
    assert file_name(person, "Acme, Inc.", "Resume", ".docx") == "Jordan_Lee_Example_Resume_Acme.docx"
    source = tmp_path / "initech_platform-engineer_1.pdf"
    source.write_bytes(b"%PDF-1.4")
    sent = sent_copy(source, tmp_path, person, "Initech", "Resume")
    assert sent.name == "Jordan_Lee_Example_Resume_Initech.pdf" and sent.read_bytes() == b"%PDF-1.4"
