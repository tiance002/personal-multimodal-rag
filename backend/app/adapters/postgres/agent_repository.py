from __future__ import annotations

import json
import uuid

from sqlalchemy import Engine, text

from backend.app.application.agent_runtime import AgentStep
from backend.app.domain.scope import Scope


class PostgresAgentRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_run(self, run_id: str, conversation_id: str, question: str, scope: Scope) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO agent_runs (id,conversation_id,knowledge_base_scope,document_scope,q0,status)
                VALUES (:id,:conversation_id,CAST(:kb AS jsonb),CAST(:docs AS jsonb),:q0,'running')"""), {
                "id": run_id,
                "conversation_id": conversation_id,
                "kb": json.dumps(list(scope.knowledge_base_ids)),
                "docs": json.dumps(list(scope.document_ids)),
                "q0": question,
            })

    def append_step(self, run_id: str, step: AgentStep) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO agent_steps (id,agent_run_id,seq,tool_name,status,input_summary,output_summary,token_count,cost_microunits)
                VALUES (:id,:run_id,:seq,:tool_name,:status,:input_summary,:output_summary,:token_count,:cost)"""), {
                "id": uuid.uuid4(), "run_id": run_id, "seq": step.seq, "tool_name": step.tool_name, "status": step.status,
                "input_summary": step.input_summary, "output_summary": step.output_summary, "token_count": step.token_count, "cost": step.cost_microunits,
            })

    def complete_run(self, run_id: str, status: str, error_code: str | None, cost_microunits: int = 0) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE agent_runs SET status=:status,step_count=(SELECT count(*) FROM agent_steps WHERE agent_run_id=:id),cost_microunits=:cost,error_code=:error_code,completed_at=now() WHERE id=:id"), {"id": run_id, "status": status, "cost": cost_microunits, "error_code": error_code})

    def cancel_run(self, run_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE agent_runs SET status='cancelled',completed_at=now(),error_code='CANCELLED' WHERE id=:id AND status IN ('running','completed','failed')"), {"id": run_id})
