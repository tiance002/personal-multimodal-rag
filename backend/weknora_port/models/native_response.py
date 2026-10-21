"""Pinned native response/stream consumers; opaque artifacts survive replay."""
from __future__ import annotations
import json
import secrets
from dataclasses import replace
from .types import ChatResponse, ToolCall, FunctionCall, ProviderError
from .usage import normalize_usage
from .usage_facts import capture_usage


def _dump(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"),sort_keys=True)


def native_usage(config,raw):
    s=config.protocol_settings.get(config.api,{})
    if raw is None:
        return normalize_usage(None,s.get("prompt_cache_accounting",False))
    if not isinstance(raw,dict): raise ProviderError("PROVIDER_USAGE_INVALID",sent=True)
    mapped={}
    if config.api=="openai-responses":
        names={"input_tokens":"prompt_tokens","output_tokens":"completion_tokens","total_tokens":"total_tokens","input_tokens_details":"prompt_tokens_details"}
        mapped={target:raw[key] for key,target in names.items() if key in raw}
    elif config.api=="anthropic-messages":
        for key in ("input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens"):
            if key in raw and type(raw[key]) is not int: raise ProviderError("PROVIDER_USAGE_INVALID",sent=True)
        if "input_tokens" in raw:
            mapped["prompt_tokens"]=raw["input_tokens"]+raw.get("cache_read_input_tokens",0)+raw.get("cache_creation_input_tokens",0)
        if "output_tokens" in raw: mapped["completion_tokens"]=raw["output_tokens"]
        for key in ("cache_read_input_tokens","cache_creation_input_tokens"):
            if key in raw: mapped[key]=raw[key]
    else:
        names={"promptTokenCount":"prompt_tokens","totalTokenCount":"total_tokens","cachedContentTokenCount":"cached_tokens"}
        mapped={target:raw[key] for key,target in names.items() if key in raw}
        if "candidatesTokenCount" in raw or "thoughtsTokenCount" in raw:
            if any(type(raw.get(k,0)) is not int for k in ("candidatesTokenCount","thoughtsTokenCount")):
                raise ProviderError("PROVIDER_USAGE_INVALID",sent=True)
            mapped["completion_tokens"]=raw.get("candidatesTokenCount",0)+raw.get("thoughtsTokenCount",0)
    return replace(normalize_usage(mapped,s.get("prompt_cache_accounting",False)),
        provider_usage_facts=capture_usage(raw,semantics=config.api+'/v1'))


def native_finish(api,reason,tools=False,incomplete=False):
    if api=="openai-responses":
        if tools: return "tool_calls"
        return {"max_output_tokens":"length","content_filter":"content_filter"}.get(reason,"length" if incomplete else "stop")
    if api=="anthropic-messages":
        if incomplete or not reason: return "incomplete"
        if reason=="max_tokens": return "length"
        if reason=="tool_use" or tools and reason=="end_turn": return "tool_calls"
        if reason=="refusal": return "content_filter"
        return reason
    if reason=="STOP": return "tool_calls" if tools else "stop"
    if reason=="MAX_TOKENS": return "length"
    if reason in ("SAFETY","RECITATION","BLOCKLIST","PROHIBITED_CONTENT"): return "content_filter"
    return reason.lower()


def google_call(part):
    fc=part["functionCall"]
    if not isinstance(fc,dict): raise ProviderError("PROVIDER_TOOL_CALL_INVALID",sent=True)
    return ToolCall(fc.get("id") or "call_"+secrets.token_hex(6),"function",
        FunctionCall(fc.get("name", ""),_dump(fc.get("args") or {})),
        {"thought_signature":part["thoughtSignature"]} if part.get("thoughtSignature") else {})


def _parse_native_response(env,config):
    content,reasoning,calls,metadata,signature=[],[],[],{},""
    if config.api=="openai-responses":
        if env.get("status")=="failed": raise ProviderError("PROVIDER_API_ERROR",sent=True)
        items=env.get("output",[])
        if not isinstance(items,list): raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
        stored=[]
        for item in items:
            if not isinstance(item,dict): raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
            if item.get("type")=="message": content.extend(p.get("text","") for p in item.get("content",[]) if p.get("type")=="output_text")
            elif item.get("type")=="reasoning":
                summary="\n".join(p["text"] for p in item.get("summary",[]) if p.get("text"))
                if summary: reasoning.append(summary)
                stored.append(item)
            elif item.get("type")=="function_call":
                calls.append(ToolCall(item.get("call_id",""),"function",FunctionCall(item.get("name",""),item.get("arguments","")),
                    {"openai_responses_item_id":item["id"]} if item.get("id") is not None else {}))
        if stored: metadata["openai_responses_reasoning"]=stored
        usage=native_usage(config,env.get("usage"))
        reason=native_finish(config.api,(env.get("incomplete_details") or {}).get("reason",""),bool(calls),env.get("status")=="incomplete")
        thinking="\n".join(reasoning)
    elif config.api=="anthropic-messages":
        blocks=env.get("content",[])
        if not isinstance(blocks,list): raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
        stored=[]
        for b in blocks:
            if not isinstance(b,dict): raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
            kind=b.get("type")
            if kind=="text": content.append(b.get("text",""))
            elif kind=="thinking":
                reasoning.append(b.get("thinking",""));stored.append({k:b[k] for k in ("type","thinking","signature") if k in b})
                if b.get("signature"): signature="anthropic-messages:"+b["signature"]
            elif kind=="redacted_thinking": stored.append({"type":kind,"data":b.get("data","")})
            elif kind=="tool_use": calls.append(ToolCall(b.get("id",""),"function",FunctionCall(b.get("name",""),_dump(b.get("input") if "input" in b else {}))))
        if stored: metadata["anthropic_thinking_blocks"]=stored
        usage=native_usage(config,env.get("usage"))
        reason=native_finish(config.api,env.get("stop_reason", ""),bool(calls))
        thinking="".join(reasoning)
    else:
        candidates=env.get("candidates")
        if not isinstance(candidates,list) or not candidates:
            raise ProviderError("PROVIDER_NO_RESPONSE",sent=True)
        candidate=candidates[0]
        for p in (candidate.get("content") or {}).get("parts",[]):
            if p.get("thoughtSignature"): signature="google-generative-ai:"+p["thoughtSignature"]
            if p.get("functionCall") is not None: calls.append(google_call(p))
            elif p.get("thought"): reasoning.append(p.get("text",""))
            else: content.append(p.get("text",""))
        usage=native_usage(config,env.get("usageMetadata"))
        reason=native_finish(config.api,candidate.get("finishReason",""),bool(calls))
        thinking="".join(reasoning)
    if any(not isinstance(t,str) for t in (*content,*reasoning)):
        raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
    return ChatResponse("".join(content),thinking,reason,tuple(calls),usage,metadata,env.get("model") or "",signature)


def parse_native_response(env,config):
    try:
        return _parse_native_response(env,config)
    except (TypeError,ValueError,KeyError,AttributeError,IndexError):
        raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True) from None


class NativeStreamState:
    def __init__(self,config,assembler):
        self.config,self.assembler=config,assembler
        self.indexes,self.args_seen,self.reasoning,self.tools={},set(),{},{}
        self.stop,self.signature,self.metadata="","",{}
        self.complete=False
    def index(self,key):
        return self.indexes.setdefault(key,len(self.indexes))
    def process(self,env):
        try:
            if self.config.api=="openai-responses": return self.responses(env)
            if self.config.api=="anthropic-messages": return self.anthropic(env)
            return self.google(env)
        except (TypeError,ValueError,KeyError,AttributeError,IndexError):
            raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True) from None
    def responses(self,e):
        a=self.assembler;kind=e.get("type","")
        if kind=="response.output_text.delta": return a.process(content=e.get("delta",""))
        if kind in ("response.reasoning_summary_text.delta","response.reasoning_text.delta"): return a.process(reasoning=e.get("delta",""))
        if kind=="response.reasoning_summary_part.added" and e.get("summary_index",0)>0: return a.process(reasoning="\n")
        if kind in ("error","response.failed"): raise ProviderError("PROVIDER_API_ERROR",sent=True)
        if kind in ("response.output_item.added","response.output_item.done"):
            item=e.get("item")
            if item is None: return []
            if not isinstance(item,dict): raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
            key=item.get("id") or "output_index:"+str(e.get("output_index",0))
            if item.get("type")=="reasoning" and kind.endswith("done"): self.reasoning[key]=item;return []
            if item.get("type")!="function_call": return []
            index=self.index(key)
            metadata={"openai_responses_item_id":item["id"]} if item.get("id") is not None else {}
            if kind.endswith("added"):
                return a.process(tools=[(index,ToolCall(item.get("call_id",""),"function",FunctionCall(item.get("name",""),""),metadata))])
            if key not in self.args_seen and item.get("arguments"):
                self.args_seen.add(key)
                return a.process(tools=[(index,ToolCall(item.get("call_id",""),"function",FunctionCall(item.get("name",""),item["arguments"]),metadata))])
            return []
        if kind=="response.function_call_arguments.delta" and e.get("delta"):
            key=e.get("item_id") or "output_index:"+str(e.get("output_index",0));self.args_seen.add(key)
            return a.process(tools=[(self.index(key),ToolCall(function=FunctionCall(arguments=e["delta"])))])
        if kind in ("response.completed","response.incomplete"):
            response=e.get("response") or {}
            if response.get("usage") is not None: a.usage=native_usage(self.config,response["usage"])
            if not self.reasoning:
                self.reasoning={str(i):item for i,item in enumerate(response.get("output",[])) if isinstance(item,dict) and item.get("type")=="reasoning"}
            self.stop=native_finish(self.config.api,(response.get("incomplete_details") or {}).get("reason",""),bool(a.calls),kind=="response.incomplete")
            self.complete=True
            return a.process(finish_reason=self.stop)
        return []
    def _thinking(self,index,kind="thinking"):
        return self.reasoning.setdefault(index,{"type":kind})
    def anthropic(self,e):
        a=self.assembler;kind=e.get("type");i=e.get("index",0)
        if kind=="message_start":
            a.usage=native_usage(self.config,(e.get("message") or {}).get("usage"));return []
        if kind=="content_block_start":
            block=e.get("content_block") or {};typ=block.get("type")
            if typ=="tool_use":
                self.tools[i]={"initial":_dump(block.get("input") or {}),"call":ToolCall(block.get("id",""),"function",FunctionCall(block.get("name",""),"")),"closed":False,"args":""}
                return a.process(tools=[(self.index(i),self.tools[i]["call"])])
            if typ=="thinking":
                self._thinking(i).update(thinking=block.get("thinking",""))
                return a.process(reasoning=block.get("thinking",""))
            if typ=="redacted_thinking": self._thinking(i,typ)["data"]=block.get("data","")
            if typ=="text": return a.process(content=block.get("text",""))
        elif kind=="content_block_delta":
            delta=e.get("delta") or {};typ=delta.get("type")
            if typ=="text_delta": return a.process(content=delta.get("text",""))
            if typ=="thinking_delta":
                block=self._thinking(i);block["thinking"]=block.get("thinking","")+delta.get("thinking","")
                return a.process(reasoning=delta.get("thinking",""))
            if typ=="signature_delta":
                block=self._thinking(i);block["signature"]=block.get("signature","")+delta.get("signature","");self.signature=block["signature"]
            if typ=="input_json_delta" and i in self.tools:
                text=delta.get("partial_json","");self.tools[i]["args"]+=text
                return a.process(tools=[(self.index(i),ToolCall(function=FunctionCall(arguments=text)))])
        elif kind=="content_block_stop" and i in self.tools:
            tool=self.tools[i];tool["closed"]=True
            if not tool["args"]: return a.process(tools=[(self.index(i),ToolCall(function=FunctionCall(arguments=tool["initial"])))])
        elif kind=="message_delta":
            self.stop=(e.get("delta") or {}).get("stop_reason") or self.stop
            if e.get("usage") is not None:
                usage=native_usage(self.config,e["usage"])
                if a.usage:
                    prompt=max(usage.prompt_tokens or 0,a.usage.prompt_tokens or 0)
                    read=max(usage.cache_read_tokens,a.usage.cache_read_tokens);write=max(usage.cache_write_tokens,a.usage.cache_write_tokens)
                    reported=usage.cache_reported or a.usage.cache_reported
                    usage=replace(usage,prompt_tokens=prompt,total_tokens=prompt+(usage.completion_tokens or 0),cache_read_tokens=read,cached_tokens=read,
                        cache_write_tokens=write,cache_miss_tokens=max(0,prompt-read),cache_reported=reported,
                        cache_status=("hit" if read else "miss") if reported else usage.cache_status,
                        status="REPORTED" if usage.completion_tokens is not None else "UNKNOWN")
                a.usage=usage
        elif kind=="message_stop": self.complete=True
        elif kind=="error": raise ProviderError("PROVIDER_API_ERROR",sent=True)
        return []
    def google(self,e):
        a=self.assembler
        if e.get("usageMetadata") is not None: a.usage=native_usage(self.config,e["usageMetadata"])
        candidates=e.get("candidates") or []
        if not candidates:
            if (e.get("promptFeedback") or {}).get("blockReason"): raise ProviderError("PROVIDER_API_ERROR",sent=True)
            return []
        cand=candidates[0];text,thinking,calls="","",[]
        for p in (cand.get("content") or {}).get("parts",[]):
            if p.get("thoughtSignature"): self.signature=p["thoughtSignature"]
            if p.get("functionCall") is not None: calls.append(google_call(p))
            elif p.get("thought"): thinking+=p.get("text","")
            else: text+=p.get("text","")
        out=a.process(content=text,reasoning=thinking) if text or thinking else []
        for call in calls:
            index=len(a.calls)
            out+=a.process(tools=[(index,replace(call,function=replace(call.function,arguments="")))])
            out+=a.process(tools=[(index,ToolCall(function=FunctionCall(arguments=call.function.arguments)))])
        if cand.get("finishReason"): out+=a.process(finish_reason=native_finish(self.config.api,cand["finishReason"],bool(a.calls)))
        return out
    def finish(self):
        a=self.assembler;out=[]
        if self.config.api=="anthropic-messages":
            a.calls={self.index(i):replace(t["call"],function=replace(t["call"].function,arguments=t["args"] or t["initial"])) for i,t in sorted(self.tools.items()) if t["closed"]}
            out=a.process(finish_reason=native_finish(self.config.api,self.stop,bool(a.calls),any(not t["closed"] for t in self.tools.values())))
            if self.reasoning: self.metadata["anthropic_thinking_blocks"]=list(self.reasoning.values())
        elif self.reasoning: self.metadata["openai_responses_reasoning"]=list(self.reasoning.values())
        if self.signature: self.metadata["reasoning_signature"]=self.config.api+":"+self.signature
        return out
