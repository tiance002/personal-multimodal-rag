"""Frozen synthetic RAG admission, isolated from product monthly budgeting."""
import hashlib
import json
import uuid
from pathlib import Path
from datetime import datetime,timezone

from backend.app.application.budget import BudgetDenied,BudgetReservation
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.application.usd_pricing import PEAK_RATES,quote_micro_usd
from backend.app.ports.session_attempts import AttemptDenied,request_identity

PHASE="task-2-real-rag-synthetic-20261001"


class RagValidationGate(ValidationAttemptGate):
    allowed_output_caps=(512,896,1024)

    def __init__(self,ledger_path,*,scope_path,scope_sha256,case_id):
        raw=Path(scope_path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=scope_sha256:
            raise AttemptDenied("RAG_SCOPE_HASH_MISMATCH")
        scope=json.loads(raw)
        if (scope.get("schema_version")!=1 or scope.get("phase")!=PHASE
                or scope.get("synthetic_only") is not True or scope.get("model") not in PEAK_RATES):
            raise AttemptDenied("RAG_SCOPE_INVALID")
        self.case=next((c for c in scope["cases"] if c["id"]==case_id),None)
        if self.case is None:raise AttemptDenied("RAG_CASE_UNREGISTERED")
        c=self.case
        if c["request_id"]!=request_identity(c["run_id"],"quick.answer",1):
            raise AttemptDenied("RAG_IDENTITY_INVALID")
        self.scope_sha256=scope_sha256
        super().__init__(ledger_path,model=scope["model"],input_tokens_cap=c["input_cap"],
                         max_output_tokens=c["max_output_tokens"])

    @property
    def cost_bound(self):
        return quote_micro_usd(PEAK_RATES[self.model],input_tokens=self.input_cap,output_tokens=self.output_cap)

    def validate_request(self,prompt,timeout_seconds,max_tokens,*,model):
        if (hashlib.sha256(prompt.encode("utf-8")).hexdigest()!=self.case["prompt_sha256"]
                or model!=self.model or max_tokens!=self.output_cap
                or type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=30):
            raise AttemptDenied("RAG_FROZEN_REQUEST_MISMATCH")

    def _phase_admit(self,data,identity):
        if identity!=self.case["request_id"]:raise AttemptDenied("RAG_IDENTITY_INVALID")
        rows=[a for a in data["attempts"] if a.get("validation_phase")==PHASE]
        if any(a.get("scope_sha256")!=self.scope_sha256 for a in rows):
            raise AttemptDenied("RAG_PHASE_REGISTRY_CHANGED")
        if len(rows)>=6:raise AttemptDenied("RAG_PHASE_CALL_LIMIT")
        if any(a.get("request_id")==identity for a in data["attempts"]):
            raise AttemptDenied("SESSION_REQUEST_ALREADY_RESERVED")

    def _admit(self,data,request_id):
        self._phase_admit(data,request_id)
        self._audit_reservation(data)

    def _audit_reservation(self,data):
        rows=[r for r in data.get("rag_validation_cost_reservations",[]) if
              r["request_id"]==self.case["request_id"] and r["state"]=="reserved"]
        if len(rows)!=1:raise AttemptDenied("RAG_DURABLE_AUDIT_RESERVATION_REQUIRED")
        return rows[0]

    def _reservation_fields(self,data):
        return {"validation_phase":PHASE,"scope_sha256":self.scope_sha256,
                "case_id":self.case["id"],"rag_audit_reservation_id":self._audit_reservation(data)["id"]}


class RagValidationBudgetGate:
    """Persisted USD audit for this scope, not fake/admitted product monthly cost."""
    def __init__(self,gate):self.gate=gate

    def reserve(self,*,run_id,provider,model_name,capability,estimate_microunits,month=None):
        g=self.gate
        if (run_id!=g.case["run_id"] or provider!="deepseek" or model_name!=g.model
                or capability!="chat" or type(estimate_microunits) is not int
                or estimate_microunits!=g.cost_bound):
            raise BudgetDenied("RAG_AUDIT_SCOPE_MISMATCH")
        with g._locked():
            data=g._read();g._used(data)
            if data.get("calls_allowed_in_this_task") is not True:
                raise BudgetDenied("SESSION_CALLS_DISABLED")
            try:g._phase_admit(data,g.case["request_id"])
            except AttemptDenied as e:raise BudgetDenied(str(e)) from None
            rows=data.setdefault("rag_validation_cost_reservations",[])
            if any(r["request_id"]==g.case["request_id"] and r["state"]!="released" for r in rows):
                raise BudgetDenied("RAG_AUDIT_ALREADY_RESERVED")
            identity=str(uuid.uuid4());date=month or datetime.now(timezone.utc).date().replace(day=1)
            rows.append({"id":identity,"request_id":g.case["request_id"],"run_id":run_id,
                "phase":PHASE,"scope_sha256":g.scope_sha256,"currency":"USD","scale":1_000_000,
                "reserved_micro_usd":estimate_microunits,"actual_charge_verified":False,
                "state":"reserved","month":date.isoformat()})
            g._write(data)
            return BudgetReservation(identity,date,estimate_microunits)

    def _change(self,identity,kind):
        g=self.gate
        with g._locked():
            data=g._read();g._used(data)
            row=next((r for r in data.get("rag_validation_cost_reservations",[]) if r["id"]==identity),None)
            if row is None:raise BudgetDenied("RAG_AUDIT_UNKNOWN_RESERVATION")
            if row["state"]!="reserved":return
            if kind=="settle":
                attempt=next((a for a in data["attempts"] if a.get("rag_audit_reservation_id")==identity),None)
                cost=attempt.get("validation_cost",{}) if attempt else {}
                if attempt and attempt["status"]=="ok" and attempt["validation_tokens"]["state"]=="settled":
                    row["state"]="usage_observed_charge_unverified"
                    row["observed_usage_peak_upper_bound_micro_usd"]=cost["observed_usage_peak_upper_bound_micro_usd"]
                else:row["state"]="unknown"
            else:row["state"]=kind
            g._write(data)

    def settle(self,reservation_id,actual_microunits):
        # Quick supplies its configured bound; it is NOT an actual charged fee.
        if actual_microunits!=self.gate.cost_bound:raise BudgetDenied("RAG_AUDIT_ESTIMATE_MISMATCH")
        self._change(reservation_id,"settle")

    def release(self,reservation_id):self._change(reservation_id,"released")
    def mark_unknown(self,reservation_id):self._change(reservation_id,"unknown")
