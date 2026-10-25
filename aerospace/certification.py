"""Compliance matrix and signed approval chain (stdlib only).

NitoCAD CANNOT certify anything. This module records human decisions by
authorised, named signers, bound to an exact design revision and assessment
digest, in an append-only hash chain. Stages above 'ready_for_engineering_review':

  engineering_review_accepted     an authorised engineering_reviewer other than the
                                  design owner accepted the assessment
  submitted_for_certification     a certification_lead recorded the submission
                                  (all compliance items satisfied, different signer)
  authority_acceptance_recorded   an authority_liaison RECORDED an acceptance
                                  reported by the certification authority, with its
                                  reference and document hash. NitoCAD does not
                                  verify the authority's decision.

Any change to the design revision or to the assessment outcome makes earlier
records stale: the stage drops back automatically.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from aerospace.evidence import digest

LADDER = ("ready_for_engineering_review", "engineering_review_accepted",
          "submitted_for_certification", "authority_acceptance_recorded")
STAGE_ROLE = {"engineering_review_accepted": "engineering_reviewer",
              "submitted_for_certification": "certification_lead",
              "authority_acceptance_recorded": "authority_liaison"}
NOTICE = ("These are records of human decisions. NitoCAD does not certify, approve or declare anything airworthy, and it does not "
          "verify decisions reported by a certification authority.")


class ApprovalError(ValueError):
    pass


def assessment_digest(a: dict) -> str:
    """Stable fingerprint of what a signer actually reviewed."""
    return digest({"revision_id": a["revision_id"], "status": a["status"], "blockers": a["blockers"], "gaps": a["gaps"],
                   "results": [{"m": r["method"], "s": r["status"], "o": r["outputs"]} for r in a.get("results", [])]})


def record_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compliance_status(items, evidence_status):
    """items: [{id, requirement_ref, title, moc: [..], evidence_ids: [..]}]; evidence_status: {id: {'current': bool, 'accepted': bool}}"""
    rows, open_n = [], 0
    for it in items or []:
        reasons = []
        if not it.get("evidence_ids"):
            reasons.append("no evidence referenced")
        for eid in it.get("evidence_ids", []):
            st = evidence_status.get(eid)
            if st is None:
                reasons.append(f"evidence {eid} not found")
            elif not st.get("current"):
                reasons.append(f"evidence {eid} is stale")
            elif not st.get("accepted"):
                reasons.append(f"evidence {eid} is not accepted")
        if not it.get("requirement_ref"):
            reasons.append("no requirement_ref")
        if not it.get("moc"):
            reasons.append("no means of compliance listed")
        rows.append({"id": it.get("id"), "requirement_ref": it.get("requirement_ref"), "status": "satisfied" if not reasons else "open", "reasons": reasons})
        open_n += bool(reasons)
    return {"items": rows, "open": open_n, "total": len(rows)}


def verify_chain(records):
    prev = None
    for i, r in enumerate(records):
        if r.get("prev_hash") != prev or r.get("hash") != record_hash(r):
            return False, f"record {i} breaks the hash chain"
        prev = r["hash"]
    return True, ""


def _valid(records, rev, dig):
    return [r for r in records if r["revision_id"] == rev and r["assessment_digest"] == dig]


def current_stage(records, assessment, certification: dict) -> dict:
    ok, why = verify_chain(records)
    if not ok:
        return {"stage": "chain_invalid", "issues": [why], "notice": NOTICE}
    rev, dig = assessment["revision_id"], assessment_digest(assessment)
    stage = LADDER[0] if assessment["status"] == "ready_for_engineering_review" else assessment["status"]
    stale = [r["stage"] for r in records if r["revision_id"] != rev or r["assessment_digest"] != dig]
    if assessment["status"] == "ready_for_engineering_review":
        have = {r["stage"] for r in _valid(records, rev, dig)}
        for nxt in LADDER[1:]:
            if nxt in have:
                stage = nxt
            else:
                break
    out = {"stage": stage, "stale_stages": sorted(set(stale)), "assessment_digest": dig, "notice": NOTICE}
    if certification and certification.get("compliance_items") is not None:
        ev = {e["id"]: e for e in assessment.get("external_evidence", []) if e.get("id")}
        out["compliance"] = compliance_status(certification["compliance_items"], ev)
    return out


def append_record(records, assessment, certification, owner, stage, signer, statement, reference=None, document_sha256=None, authority_name=None):
    """Validate and build the next record (does not persist). Raises ApprovalError."""
    if stage not in STAGE_ROLE:
        raise ApprovalError(f"stage must be one of {', '.join(STAGE_ROLE)}")
    ok, why = verify_chain(records)
    if not ok:
        raise ApprovalError(why)
    if assessment["status"] != "ready_for_engineering_review":
        raise ApprovalError(f"the design is '{assessment['status']}', not ready_for_engineering_review: no approval can be recorded")
    if not str(statement or "").strip():
        raise ApprovalError("a statement describing what is being approved is required")
    role = STAGE_ROLE[stage]
    allowed = [a.lower() for a in (certification or {}).get("authorized_signers", {}).get(role, [])]
    if signer.lower() not in allowed:
        raise ApprovalError(f"{signer} is not an authorised {role} for this design")
    rev, dig = assessment["revision_id"], assessment_digest(assessment)
    valid = _valid(records, rev, dig)
    by_stage = {r["stage"]: r for r in valid}
    idx = LADDER.index(stage)
    if idx > 1 and LADDER[idx - 1] not in by_stage:
        raise ApprovalError(f"stage {LADDER[idx - 1]} must be recorded first for this exact revision and assessment")
    if stage == "engineering_review_accepted" and signer.lower() == owner.lower():
        raise ApprovalError("the design owner cannot accept their own design (separation of duties)")
    if stage != "engineering_review_accepted" and signer.lower() in [r["signer"].lower() for r in valid if r["stage"] != stage]:
        raise ApprovalError("a different person must sign each stage (separation of duties)")
    if stage == "submitted_for_certification":
        comp = compliance_status((certification or {}).get("compliance_items"), {e["id"]: e for e in assessment.get("external_evidence", []) if e.get("id")})
        if comp["total"] == 0 or comp["open"]:
            raise ApprovalError(f"the compliance matrix must be complete and satisfied ({comp['open']} of {comp['total']} items open)")
    if stage == "authority_acceptance_recorded":
        if not str(reference or "").strip() or not str(authority_name or "").strip():
            raise ApprovalError("the authority name and its reference number are required")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(document_sha256 or "")):
            raise ApprovalError("document_sha256 must be the SHA-256 of the authority's document")
    rec = {"design_id": assessment["design_id"], "stage": stage, "role": role, "signer": signer, "statement": statement.strip(),
           "revision_id": rev, "assessment_digest": dig, "reference": reference, "authority_name": authority_name,
           "document_sha256": document_sha256, "created_at": datetime.now(timezone.utc).isoformat(),
           "prev_hash": records[-1]["hash"] if records else None}
    rec["hash"] = record_hash(rec)
    return rec
