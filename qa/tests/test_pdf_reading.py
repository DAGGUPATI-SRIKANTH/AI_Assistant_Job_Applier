"""A PDF resume read back whole: words unbroken, a wrapped bullet one bullet, a title beside its dates."""

from ml.tailoring.reader import logical_lines, mend_words

# pypdf's reading: lines in place, words broken at letter gaps.
PYPDF = """PLATFORM ENGINEER, ACME, USA  Mar 2021 – Present
• Ran Cloud Bui ld and SonarQ ube gates for high -stakes releases, using Jenkins,
GitLab CI, and canary frameworks , cutting rollbacks by 10 –
15%.
• Kept the release calen dar steady.
Monitoring & Logging: Prometheus, Grafana, Loki,
Alertmanager, Tempo
Languages: Python, Bash"""
# pdfminer's reading of the same page: words whole, dates moved to a column of their own.
PDFMINER = """PLATFORM ENGINEER, ACME, USA
• Ran Cloud Build and SonarQube gates for high-stakes releases, using Jenkins,
GitLab CI, and canary frameworks, cutting rollbacks by 10–
15%.
• Kept the release calendar steady.
Monitoring & Logging: Prometheus, Grafana, Loki,
Alertmanager, Tempo
Languages: Python, Bash
Mar 2021 – Present"""


def test_broken_words_are_mended_and_lines_kept_in_place():
    text = mend_words(PYPDF, PDFMINER)
    assert text.splitlines()[0] == "PLATFORM ENGINEER, ACME, USA Mar 2021 – Present"   # dates stay beside it
    for whole in ("Cloud Build and", "SonarQube", "high-stakes", "frameworks,", "calendar", "10–"):
        assert whole in text, whole
    assert "Cloud Run" in mend_words("Cloud Run jobs", "Cloud Run jobs")                # two words stay two


def test_a_wrapped_bullet_comes_back_as_one():
    lines = logical_lines(mend_words(PYPDF, PDFMINER))
    assert lines[1].startswith("• Ran Cloud Build") and lines[1].endswith("cutting rollbacks by 10–15%.")
    assert lines[2] == "• Kept the release calendar steady."
    assert lines[3] == "Monitoring & Logging: Prometheus, Grafana, Loki, Alertmanager, Tempo"
    assert lines[4] == "Languages: Python, Bash"
