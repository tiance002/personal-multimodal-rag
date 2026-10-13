"""Execute fixed upstream AES helper offline; Go is reference tooling only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r'E:\RAG-reference\WeKnora-3e8b0bfc\internal\utils\crypto.go')
GO = Path(r'E:\RAG-reference\toolchains\go1.26.0\go\bin\go.exe')


def run():
    source = SOURCE.read_text(encoding='utf-8')
    program = source.replace('package utils','package main',1).replace('import (','import (\n"bytes"\n"encoding/json"\n"fmt"',1)
    program += '''
func main() {
 key := []byte("01234567890123456789012345678901")
 rand.Reader = bytes.NewReader([]byte("012345678901"))
 encrypted, err := EncryptAESGCM("synthetic-secret",key)
 if err != nil { panic(err) }
 plain, err := DecryptAESGCM(encrypted,key)
 if err != nil { panic(err) }
 os.Setenv("SYSTEM_AES_KEY", "")
 _, strictError := DecryptStoredSecret(encrypted)
 lenient, ok := DecryptStoredSecretLenient(encrypted)
 output := map[string]interface{}{"ciphertext":encrypted,"plaintext":plain,"missing_key_strict_error":strictError!=nil,"missing_key_lenient":lenient,"missing_key_lenient_ok":ok}
 result,_ := json.Marshal(output)
 fmt.Println(string(result))
}
'''
    target = ROOT / 'var/weknora-port/crypto-oracle'
    target.mkdir(parents=True,exist_ok=True)
    (target/'main.go').write_text(program,encoding='utf-8',newline='\n')
    env = dict(os.environ,GOCACHE=str(target/'cache'),GOTOOLCHAIN='local')
    observed = subprocess.run([str(GO),'run','main.go'],cwd=target,env=env,text=True,capture_output=True,check=True)
    output = {'upstream_commit':'3e8b0bfc80b845b2d4b2ed683994748741450a97',
              'source':'internal/utils/crypto.go','source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'oracle':'EXECUTED_FIXED_SOURCE_STDLIB_GO','exit_code':observed.returncode,
              'case':json.loads(observed.stdout)}
    fixture = ROOT/'tests/weknora_port/fixtures/crypto_oracle.json'
    fixture.parent.mkdir(parents=True,exist_ok=True)
    fixture.write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(output,indent=2))


if __name__ == '__main__': run()
