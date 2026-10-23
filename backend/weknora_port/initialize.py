"""Explicit, empty-new-database-only initialization. Never runs during API startup."""
import argparse,asyncio,json
from .development import DevelopmentSettings,PROHIBITED,DevelopmentConfigurationError

async def initialize(settings):
    import psycopg
    from psycopg.rows import dict_row
    from .storage.postgres import PostgresRetriever,IsolationPolicy
    from .storage.migrate import apply_initial_schema,apply_history_and_memory_schema
    from .storage.configuration_migrate import apply_configuration_schema
    from .storage.generation_migrate import apply_generation_schema
    from .storage.send_gate_migrate import apply_send_gate_schema
    from .storage.turns_migrate import apply_turn_schema,apply_turn_configuration_schema
    from .storage.models_migrate import apply_model_schema
    from .storage.document_profile_migrate import apply_document_profile_schema
    from .storage.image_tasks_migrate import apply_image_task_schema,apply_image_selected_retry_schema
    from .storage.token_migrate import apply_token_schema
    connection=await psycopg.AsyncConnection.connect(settings.database_url,autocommit=True,row_factory=dict_row,connect_timeout=5)
    try:
        guard=PostgresRetriever(connection,IsolationPolicy(settings.system_id,PROHIBITED,settings.database))
        await guard.verify_identity()
        row=await (await connection.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'")).fetchone()
        if row['n']:raise DevelopmentConfigurationError('EMPTY_NEW_DATABASE_REQUIRED_NO_DDL_EXECUTED')
        result={'retrieval':await apply_initial_schema(guard,[]),'history_memory':await apply_history_and_memory_schema(guard)}
        for apply in (apply_configuration_schema,apply_generation_schema,apply_send_gate_schema,apply_turn_schema,
                      apply_model_schema,apply_document_profile_schema,apply_image_task_schema,apply_image_selected_retry_schema,apply_turn_configuration_schema,apply_token_schema):
            result[apply.__name__]=await apply(guard)
        return result
    finally:await connection.close()

def main():
    parser=argparse.ArgumentParser(description='Initialize only an empty, explicitly identified new ParadeDB database')
    parser.add_argument('--empty-new-database',action='store_true',required=True)
    parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:result=runner.run(initialize(DevelopmentSettings.from_environment()))
    print(json.dumps(result))

if __name__=='__main__':main()
