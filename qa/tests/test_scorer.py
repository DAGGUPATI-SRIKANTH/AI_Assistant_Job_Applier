"""What the score means, and what it must refuse to reward.

`score_resume` measures how much of a posting's salient vocabulary the resume
covers. On its own that is blind to *which job* the posting is for, and these
tests pin the correction.
"""

import pytest

from ml.resume.scorer import WRONG_ROLE_FLOOR, role_fit, score_resume

# --- role fit -------------------------------------------------------------
# A resume and a job description can share almost all their vocabulary and
# still be different jobs: a data engineer and a DevSecOps engineer both write
# AWS, Python, Kubernetes, Terraform and CI/CD. Measured on live data, that
# scored a DevSecOps posting 93% for a data engineer's resume. These tests
# pin the thing that tells them apart -- the title.

def test_role_fit_exact_title_is_full_credit():
    assert role_fit("Senior Data Engineer", ["data engineer"]) == 1.0


def test_role_fit_held_title_beats_the_search_list():
    # He has actually been one, so it is his job whatever the search says.
    assert role_fit("Curator of Manuscripts", [], ["curator of manuscripts"]) == 1.0


def test_role_fit_scattered_words_earn_partial_credit():
    # "Engineer, Data Platform" is the job; "Data Platform Engineer" is the
    # search. Same role, words out of order -- and the comma must not matter.
    assert role_fit("Engineer, Data Platform", ["data platform engineer"]) == 0.85
    assert role_fit("Engineer - Cloud Infrastructure", ["cloud infrastructure"]) == 1.0
    assert role_fit("Engineer (Data Platform)", ["data platform"]) == 1.0


def test_role_fit_rejects_a_different_field():
    assert role_fit("Senior DevSecOps Engineer", ["data engineer"]) == 0.0


def test_role_fit_does_not_match_a_longer_word():
    # "sre" must not be found inside "Presenter" or "Stressed".
    assert role_fit("Presenter", ["sre"]) == 0.0


def test_role_fit_with_no_titles_is_neutral():
    # No profile means no opinion -- it must not penalise every job.
    assert role_fit("Anything At All", [], []) == 0.0
    assert role_fit("", ["data engineer"]) == 1.0


def test_score_resume_penalises_the_wrong_role():
    resume = "Data engineer. Python, Spark, Airflow, AWS, Kubernetes, Terraform, CI/CD."
    jd = "Python, Spark, Airflow, AWS, Kubernetes, Terraform, CI/CD required."
    right = score_resume(resume, jd, job_title="Data Engineer",
                         search_titles=["data engineer"])
    wrong = score_resume(resume, jd, job_title="DevSecOps Engineer",
                         search_titles=["data engineer"])
    assert right.score > wrong.score
    # Penalised, not erased: the skills really are shared.
    assert wrong.score == pytest.approx(right.score * WRONG_ROLE_FLOOR, abs=1e-3)


# --- postings with no description -----------------------------------------

def test_a_posting_with_no_description_cannot_score_full_marks():
    """A real posting sat at the top of a 2,482-job list: "Embedded Systems
    Engineer", 25 characters of description, scored 1.00 -- above every job
    that had actually been measured. With no text, coverage is undefined, and
    reporting 100% claims certainty from no evidence."""
    from ml.resume.scorer import TITLE_ONLY_CEILING, score_basis

    resume = "Systems engineer. Kubernetes, AWS, Terraform, Python."
    thin = score_resume(resume, "Embedded Systems Engineer",
                        job_title="Embedded Systems Engineer",
                        search_titles=["systems engineer"])
    assert score_basis("Embedded Systems Engineer") == "title"
    assert thin.score <= TITLE_ONLY_CEILING


def test_a_real_description_is_not_capped():
    resume = "DevOps engineer. Kubernetes, AWS, Terraform, Python, CI/CD."
    jd = ("We need a DevOps engineer with Kubernetes, AWS, Terraform and "
          "Python for our CI/CD platform. ") * 12
    from ml.resume.scorer import TITLE_ONLY_CEILING

    full = score_resume(resume, jd, job_title="DevOps Engineer",
                        search_titles=["devops"])
    assert full.score > TITLE_ONLY_CEILING


def test_the_cap_sits_below_the_default_threshold():
    """So an unmeasurable posting reads as "worth a look", not "best match"."""
    from ml.resume.scorer import TITLE_ONLY_CEILING

    assert TITLE_ONLY_CEILING < 0.60


# --- every field, not only tech ---------------------------------------------
# Only tech skills were weighted, so in a nursing posting job-ad prose outweighed
# everything a nurse's resume matched: a registered nurse scored at most 30%
# against registered nurse postings. Each field's licenses, tools and core words
# now weigh what a tech skill weighs -- for people in that field.

NURSING_JD = ("We are hiring a Registered Nurse (RN) for our telemetry unit. BLS and ACLS "
              "certification required. You will assess patients, give medications and chart "
              "in the EMR. Strong communicator who maintains standards and supports others. ") * 4
NURSE_RESUME = ("Registered Nurse, 9 years on a telemetry unit. Certifications: BLS, ACLS. "
                "Assess patients, administer medication, chart in Epic EMR.")


def test_plurals_match_both_ways():
    from ml.resume.scorer import fold, present
    have = present("Certifications: BLS. Cared for patients.")
    assert "certification" in have and "patient" in have and "patients" in have
    assert fold("therapies") == "therapy" and fold("boxes") == "box"
    for word in ("process", "status", "analysis", "aws", "bls"):     # only look plural
        assert fold(word) == word


def test_each_word_is_weighed_once_whatever_its_forms():
    import math

    from ml.resume.scorer import jd_keyword_weights
    weights = jd_keyword_weights("Patient care. Our patients come first. Patients and patient safety.",
                                 fields={"healthcare"})
    forms = [term for term in weights if term.startswith("patient")]
    assert len(forms) == 1                      # one entry, shown as the posting writes it most
    assert weights[forms[0]] == pytest.approx(1 + math.log(4))     # fewer than 3 field words: no field


def test_a_fields_licenses_weigh_what_a_tech_skill_weighs():
    from ml.resume.scorer import jd_keyword_weights
    nursing = jd_keyword_weights(NURSING_JD, fields={"healthcare"})
    tech = jd_keyword_weights("We need AWS and Terraform. Strong communicator. " * 4)
    assert nursing["bls"] == pytest.approx(tech["aws"])
    assert {"rn", "acls", "emr", "telemetry"} <= set(nursing)
    tops = list(nursing)[:8]
    assert "communicator" not in tops and "maintains" not in tops


def test_a_resume_with_the_fields_licenses_scores_higher_once_they_count():
    as_nurse = score_resume(NURSE_RESUME, NURSING_JD, job_title="Registered Nurse",
                            search_titles=["registered nurse"], fields={"healthcare"})
    unweighted = score_resume(NURSE_RESUME, NURSING_JD, job_title="Registered Nurse",
                              search_titles=["registered nurse"], fields=frozenset())
    assert as_nurse.score > unweighted.score
    assert {"bls", "acls", "emr", "telemetry", "patients"} <= set(as_nurse.matched)


def test_a_field_counts_for_its_own_people_only():
    """A DevOps engineer applying to a bank is not screened for "fraud" or "deposit"."""
    from ml.resume.scorer import fields_of, jd_keyword_weights
    bank = ("DevOps engineer for our bank: Kubernetes, Terraform, AWS. You will support audit, "
            "fraud and banking compliance, loan and deposit systems. ") * 3
    as_devops = jd_keyword_weights(bank, fields=fields_of(["devops", "cloud"]))
    unknown = jd_keyword_weights(bank)                      # no profile: the posting decides
    assert as_devops["kubernetes"] > as_devops["audit"]
    assert unknown["audit"] == pytest.approx(as_devops["audit"] * 2.5)
    assert fields_of(["devops", "cloud"]) == frozenset() and fields_of(None) is None
    assert fields_of(["healthcare", "devops"]) == {"healthcare"}


def test_one_mention_does_not_make_a_posting_part_of_a_field():
    from ml.resume.scorer import jd_keyword_weights
    travel = ("Our platform books hotels for travelers. We need a DevOps engineer with Kubernetes, "
              "Terraform and AWS to keep the booking systems up. Hotels depend on us. ") * 3
    weights = jd_keyword_weights(travel)
    assert weights["kubernetes"] > weights["hotels"]


def test_the_keyword_list_and_the_score_agree_on_plurals():
    from ml.tailoring.keywords import _has
    assert "certification" in _has("Certifications: BLS, ACLS")
