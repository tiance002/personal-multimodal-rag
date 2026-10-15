"""Reproduce only the approved native Office fixture; no credentials are logged."""
import os,re,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
from redis import Redis
root=Path(__file__).resolve().parents[4]
evidence=Path(__file__).resolve().parent
assert root.name == "weknora-python-port-20261013"
os.chdir(root)
text=(root/"docs/weknora-port/AUTOMATIC_MEMORY_CHECKPOINT_20261013.md").read_text(encoding="utf-8")
pattern=r"postgres(?:ql)?://[^\s\x27\x22`]+"
dsn=next(v for v in re.findall(pattern,text) if urlsplit(v).hostname=="127.0.0.1" and urlsplit(v).port==56423 and urlsplit(v).path=="/weknora_python_port_20261013")
info=subprocess.check_output(["docker","exec","weknora-office-legacy-test-20261015","redis-cli","INFO","server"],text=True)
run_id=re.search(r"^run_id:([0-9a-f]{40})",info,re.M).group(1)
client=Redis.from_url("redis://127.0.0.1:56425/15",socket_connect_timeout=3)
assert client.info("server")["run_id"]==run_id
client.close()
env=dict(os.environ,PYTHONIOENCODING="utf-8",PYTHONDONTWRITEBYTECODE="1",TEMP="E:/WK/run/office-legacy-temp-20261015",TMP="E:/WK/run/office-legacy-temp-20261015",WEKNORA_PORT_TEST_DSN=dsn,WEKNORA_PORT_TEST_REDIS_RUN_ID=run_id,WEKNORA_OFFICE_TEST_RECEIPT="accepted-native-pg.json")
env["PYTHONPATH"]=os.pathsep.join([str(root),"E:/WK/deps/reader",str(root/"var/weknora-port/runtime-deps")])
args=[sys.executable,"-B","-m","pytest","--noconftest","-q","tests/weknora_port/test_office_legacy_pg.py","--tb=short","-p","no:cacheprovider","--basetemp=E:/WK/run/office-legacy-temp-20261015/pytest-accepted","--junitxml="+str(evidence/"accepted-native.junit.xml")]
(evidence/"accepted-native.command.txt").write_text(subprocess.list2cmdline(args)+"\nTEMP=TMP="+env["TEMP"]+"\nPYTHONPATH="+env["PYTHONPATH"]+"\nIsolated DSN and new Redis INFO identity checked; credentials not logged.\n",encoding="utf-8")
with (evidence/"accepted-native.stdout.txt").open("w",encoding="utf-8") as output:
    result=subprocess.run(args,env=env,stdout=output,stderr=subprocess.STDOUT)
(evidence/"accepted-native.exitcode.txt").write_text(str(result.returncode)+"\n",encoding="utf-8")
print((evidence/"accepted-native.stdout.txt").read_text(encoding="utf-8"))
raise SystemExit(result.returncode)
