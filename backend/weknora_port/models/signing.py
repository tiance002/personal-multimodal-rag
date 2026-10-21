"""Fixed WeKnora Cloud signature, with injectable clock/nonce for fixtures."""
import hashlib
import hmac
import secrets
import string
import time
import uuid
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qsl,urlencode,quote,unquote
from .types import ProviderError


def _encode(value):
    # Source signer encodes runes (rather than UTF8 bytes); retain exact behavior.
    return "".join(c if c in string.ascii_letters+string.digits+"-_.~" else "%"+format(ord(c),"02X") for c in value)


def weknora_cloud_headers(app_id,secret,wire,*,request_id=None,timestamp=None,nonce=None):
    if not app_id or not secret: raise ProviderError("MODEL_CREDENTIAL_REQUIRED")
    request_id=request_id or str(uuid.uuid4())
    timestamp=str(int(time.time()) if timestamp is None else timestamp)
    nonce=nonce or "".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16))
    params={"x-appid":app_id,"x-api-key":secret,"x-request-id":request_id,"x-timestamp":timestamp,
            "x-nonce":nonce,"body":hashlib.md5(wire or b"{}").hexdigest()}
    canonical="&".join(_encode(k)+"="+_encode(v) for k,v in sorted(params.items()))
    return {"X-APPID":app_id,"X-API-Key":secret,"X-Request-ID":request_id,"X-Timestamp":timestamp,
            "X-Nonce":nonce,"X-Signature":hashlib.md5(canonical.encode()).hexdigest()}


def _hmac(key,text):
    return hmac.new(key,text.encode(),hashlib.sha256).digest()


def tencent_lkeap_headers(secret_id,secret,wire,*,region="ap-guangzhou",timestamp=None):
    if not secret_id or not secret: raise ProviderError("MODEL_CREDENTIAL_REQUIRED")
    timestamp=int(time.time()) if timestamp is None else timestamp
    date=datetime.fromtimestamp(timestamp,timezone.utc).strftime("%Y-%m-%d")
    host="lkeap.tencentcloudapi.com"
    canonical="POST\n/\n\ncontent-type:application/json\nhost:"+host+"\n\ncontent-type;host\n"+hashlib.sha256(wire).hexdigest()
    scope=date+"/lkeap/tc3_request"
    tosign="TC3-HMAC-SHA256\n"+str(timestamp)+"\n"+scope+"\n"+hashlib.sha256(canonical.encode()).hexdigest()
    key=_hmac(_hmac(_hmac(("TC3"+secret).encode(),date),"lkeap"),"tc3_request")
    signature=_hmac(key,tosign).hex()
    return {"Content-Type":"application/json","X-TC-Action":"RunRerank","X-TC-Version":"2024-05-22",
            "X-TC-Timestamp":str(timestamp),"X-TC-Region":region or "ap-guangzhou","X-TC-Language":"en-US",
            "X-TC-RequestClient":"SDK_GO_1.3.103",
            "Authorization":"TC3-HMAC-SHA256 Credential="+secret_id+"/"+scope+", SignedHeaders=content-type;host, Signature="+signature}


def volcengine_headers(access_key,secret,wire,url,headers=None,*,region="cn-beijing",timestamp=None):
    if not access_key or not secret: raise ProviderError("MODEL_CREDENTIAL_REQUIRED")
    date=datetime.fromtimestamp(time.time() if timestamp is None else timestamp,timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts=urlsplit(url);host=parts.netloc
    if parts.port in (80,443): host=parts.hostname
    sha=hashlib.sha256(wire).hexdigest()
    values={**dict(headers or {}),"Content-Type":"application/json","Host":host,"X-Date":date,"X-Content-Sha256":sha}
    signed={k.lower():v.strip() for k,v in values.items() if k in ("Content-Type","Content-Md5","Host","X-Security-Token") or k.startswith("X-")}
    keys=";".join(sorted(signed))
    canonical_headers="".join(k+":"+signed[k]+"\n" for k in sorted(signed))
    path="/".join(quote(unquote(fragment),safe="-_.~") for fragment in (parts.path or "/").split("/"))
    query=urlencode(sorted(parse_qsl(parts.query,keep_blank_values=True))).replace("+","%20")
    canonical="\n".join(("POST",path,query,canonical_headers,keys,sha))
    scope=date[:8]+"/"+(region or "cn-beijing")+"/air/request"
    text="HMAC-SHA256\n"+date+"\n"+scope+"\n"+hashlib.sha256(canonical.encode()).hexdigest()
    key=_hmac(_hmac(_hmac(_hmac(secret.encode(),date[:8]),region or "cn-beijing"),"air"),"request")
    return {"Content-Type":"application/json","X-Date":date,"X-Content-Sha256":sha,
            "Authorization":"HMAC-SHA256 Credential="+access_key+"/"+scope+", SignedHeaders="+keys+", Signature="+_hmac(key,text).hex()}
