"""Explicit expected identity for the existing dedicated Redis test target."""
import os,re
LEGACY_REDIS_RUN_ID='586b92f4649b78508382dfd52d64d848f45b0a7c'
def expected_redis_run_id():
    value=os.environ.get('WEKNORA_PORT_TEST_REDIS_RUN_ID',LEGACY_REDIS_RUN_ID)
    if not re.fullmatch('[0-9a-f]{40}',value):raise ValueError('EXPLICIT_REDIS_RUN_ID_INVALID')
    return value
