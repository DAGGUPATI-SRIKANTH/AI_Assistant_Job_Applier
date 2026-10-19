""""I've used this": the person's own line, merged under their job, never invented."""

import pytest

from ml.resume import additions
from ml.resume.additions import NotAdded
from ml.resume.parser import parse_text

MASTER = """JORDAN EXAMPLE
jordan@example.com

EXPERIENCE
PLATFORM ENGINEER, ACME, USA     Jan 2022 – Present
- Built Terraform modules for 200+ AWS workloads.
- Ran Kubernetes clusters on EKS.
SYSTEMS ENGINEER, GLOBEX, USA     Jan 2016 – Dec 2021
- Maintained VMware clusters.
"""
ROLE = "PLATFORM ENGINEER, ACME, USA     Jan 2022 – Present"


@pytest.fixture
def store(tmp_path):
    return tmp_path / "additions.yaml"


def test_the_jobs_come_from_the_resume():
    assert additions.roles(parse_text(MASTER))[0] == ROLE


def test_degrees_are_not_jobs():
    with_degree = MASTER + "\nEDUCATION\nMaster of Science     Jan 2019 – Dec 2020\n"
    assert all("Master" not in r for r in additions.roles(parse_text(with_degree)))


def test_a_line_is_merged_under_its_job_and_nowhere_else(store):
    additions.add(parse_text(MASTER), ROLE, "Defined Protobuf schemas for service contracts", "Protobuf",
                  True, path=store)
    resume = additions.apply(parse_text(MASTER), path=store)
    lines = [l for s in resume.sections for l in s.lines]
    acme = lines.index(ROLE)
    globex = next(i for i, l in enumerate(lines) if l.startswith("SYSTEMS ENGINEER"))
    assert lines.index("- Defined Protobuf schemas for service contracts.") in range(acme, globex)
    assert "Protobuf" in resume.text()                    # the guard and ATS check see it


@pytest.mark.parametrize("role,line,tool,confirmed,why", [
    (ROLE, "Defined Protobuf schemas for service contracts", "Protobuf", False, "Tick"),
    ("MADE UP JOB, NOWHERE", "Defined Protobuf schemas for service contracts", "Protobuf", True, "Pick"),
    (ROLE, "Used it", "Protobuf", True, "full line"),
    (ROLE, "Defined schemas for all service contracts at scale", "Protobuf", True, "Mention Protobuf"),
])
def test_what_cannot_be_added(store, role, line, tool, confirmed, why):
    with pytest.raises(NotAdded, match=why):
        additions.add(parse_text(MASTER), role, line, tool, confirmed, path=store)
    assert additions.load(store) == []


def test_a_job_picked_in_the_browser_matches_despite_its_spacing(store):
    """A dropdown sends "USA Jan 2022" for the resume's "USA     Jan 2022"; the resume's own line is kept."""
    entry = additions.add(parse_text(MASTER), "PLATFORM ENGINEER, ACME, USA Jan 2022 – Present",
                          "Patched and monitored a fleet of Linux servers", "Linux", True, path=store)
    assert entry["role"] == ROLE
    with pytest.raises(NotAdded, match="Pick one"):
        additions.add(parse_text(MASTER), "PLATFORM ENGINEER, ACME Jan 2022", "Patched Linux servers every week",
                      "Linux", True, path=store)


def test_a_kind_of_work_is_mentioned_by_its_words_in_any_order(store):
    entry = additions.add(parse_text(MASTER), ROLE, "Managed a fleet of Linux servers, patching and monitoring them",
                          "linux fleet", True, path=store)
    assert entry["tool"] == "linux fleet"
    assert additions.mentions("Ran open-source tooling for endpoints", "open-source")
    assert not additions.mentions("Managed Windows servers across the fleet", "linux fleet")
    assert not additions.mentions("Defined schemas for all service contracts", "Protobuf")


def test_the_same_line_twice_is_refused_and_it_can_be_removed(store):
    base = parse_text(MASTER)
    additions.add(base, ROLE, "Defined Protobuf schemas for service contracts", "Protobuf", True, path=store)
    with pytest.raises(NotAdded, match="already"):
        additions.add(base, ROLE, "Defined Protobuf schemas for service contracts.", "Protobuf", True, path=store)
    assert additions.remove(ROLE, "Defined Protobuf schemas for service contracts.", path=store)
    assert additions.load(store) == []


def test_the_route_refuses_an_unticked_line(monkeypatch, store):
    from backend.api import app as webapp
    monkeypatch.setattr(additions, "ADDITIONS_PATH", store)
    monkeypatch.setattr("ml.resume.pipeline.load_base_resume", lambda cfg: parse_text(MASTER))
    monkeypatch.setattr(webapp, "cfg", lambda: None)   # the real one loads .env into the environment
    client = webapp.app.test_client()
    bad = client.post("/resume/additions", json={"tool": "Protobuf", "role": ROLE,
                                                 "line": "Defined Protobuf schemas for contracts", "confirmed": False})
    assert bad.status_code == 400 and "Tick" in bad.get_json()["error"]
    good = client.post("/resume/additions", json={"tool": "Protobuf", "role": ROLE,
                                                  "line": "Defined Protobuf schemas for contracts", "confirmed": True})
    assert good.get_json()["ok"] and additions.load(store)[0]["tool"] == "Protobuf"


def test_one_line_can_go_under_every_job_ticked(monkeypatch, store):
    from backend.api import app as webapp
    monkeypatch.setattr(additions, "ADDITIONS_PATH", store)
    monkeypatch.setattr("ml.resume.pipeline.load_base_resume", lambda cfg: parse_text(MASTER))
    monkeypatch.setattr(webapp, "cfg", lambda: None)
    client = webapp.app.test_client()
    both = additions.roles(parse_text(MASTER))
    reply = client.post("/resume/additions", json={"tool": "Linux", "roles": both, "confirmed": True,
                                                   "line": "Patched and monitored a fleet of Linux servers"})
    assert reply.get_json()["ok"] and "2 jobs" in reply.get_json()["said"]
    assert sorted(e["role"] for e in additions.load(store)) == sorted(both)
    again = client.post("/resume/additions", json={"tool": "Linux", "roles": both, "confirmed": True,
                                                   "line": "Patched and monitored a fleet of Linux servers"})
    assert again.status_code == 400 and "already" in again.get_json()["error"]


def test_each_job_ticked_can_have_its_own_line(monkeypatch, store):
    """The lines written for "I've used this" differ by job: each goes under its own."""
    from backend.api import app as webapp
    monkeypatch.setattr(additions, "ADDITIONS_PATH", store)
    monkeypatch.setattr("ml.resume.pipeline.load_base_resume", lambda cfg: parse_text(MASTER))
    monkeypatch.setattr(webapp, "cfg", lambda: None)
    client = webapp.app.test_client()
    acme, globex = additions.roles(parse_text(MASTER))
    reply = client.post("/resume/additions", json={"tool": "Ansible", "confirmed": True, "items": [
        {"role": acme, "line": "Wrote Ansible playbooks that patched the EKS worker nodes"},
        {"role": globex, "line": "Used Ansible to configure the VMware cluster hosts"}]})
    assert reply.get_json()["ok"] and "2 jobs" in reply.get_json()["said"]
    assert {e["role"]: e["line"] for e in additions.load(store)} == {
        acme: "Wrote Ansible playbooks that patched the EKS worker nodes.",
        globex: "Used Ansible to configure the VMware cluster hosts."}
    unticked = client.post("/resume/additions", json={"tool": "Ansible", "confirmed": False, "items": [
        {"role": acme, "line": "Ran Ansible against the EKS worker nodes every week"}]})
    assert unticked.status_code == 400 and "Tick" in unticked.get_json()["error"]
