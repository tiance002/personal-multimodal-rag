"""Pinned Responses, Anthropic Messages and Gemini request renderers (MIT)."""
from __future__ import annotations
import base64
import json
import mimetypes
from copy import deepcopy
from urllib.parse import unquote, urlsplit
from .request import neutralize, clamp_level, supports_level, LADDER
from .types import ProviderError


def _json(value, fallback=None):
    if not isinstance(value,str):
        return deepcopy(value)
    try:
        return json.loads(value)
    except (ValueError,TypeError):
        return fallback


def _opaque_string(value):
    decoded=_json(value,value)
    return decoded if isinstance(decoded,str) else ""


def _level(config,opts):
    level,requested=opts.reasoning() if opts else ("",False)
    return (clamp_level(config.thinking_levels,level) if level in LADDER else level),requested


def _value(config,level):
    return config.thinking_levels.get(level,level) or ""


def _parts(msg,resolve):
    if msg.multi_content:
        return [{**p,"text":neutralize(p.get("text",""))} if p.get("type")=="text" else
                {**p,"image_url":{**p["image_url"],"url":resolve(p["image_url"]["url"])}} if p.get("image_url") else p
                for p in msg.multi_content]
    if msg.images:
        return [{"type":"image_url","image_url":{"url":resolve(url),"detail":"auto"}} for url in msg.images]+[{"type":"text","text":neutralize(msg.content)}]
    return [{"type":"text","text":neutralize(msg.content)}]


def _extra(body,settings,key="extra_body"):
    for k,v in settings.get(key,{}).items():
        body.setdefault(k,deepcopy(v))
    return body


def responses_body(config,messages,opts,stream,resolve):
    s=config.protocol_settings.get(config.api,{})
    instructions,items=[],[]
    for msg in messages:
        if msg.role=="system":
            if msg.content: instructions.append(neutralize(msg.content))
        elif msg.role=="assistant":
            stored=_json(msg.reasoning_metadata.get("openai_responses_reasoning",[]),[])
            if isinstance(stored,list): items.extend(deepcopy(item) for item in stored if item is not None)
            if msg.content or not msg.tool_calls:
                items.append({"type":"message","role":"assistant","content":[{"type":"output_text","text":neutralize(msg.content)}]})
            for tc in msg.tool_calls:
                item={"type":"function_call","call_id":tc.id,"name":tc.function.name,"arguments":neutralize(tc.function.arguments)}
                if tc.provider_metadata.get("openai_responses_item_id") is not None:
                    item["id"]=_opaque_string(tc.provider_metadata["openai_responses_item_id"])
                items.append(item)
        elif msg.role=="tool":
            items.append({"type":"function_call_output","call_id":msg.tool_call_id,"output":neutralize(msg.content)})
        else:
            content=neutralize(msg.content)
            if msg.multi_content or msg.images:
                content=[]
                for p in _parts(msg,resolve):
                    if p.get("type")=="text":
                        content.append({"type":"input_text",**({"text":p["text"]} if p.get("text") else {})})
                    elif p.get("image_url"):
                        content.append({"type":"input_image","image_url":p["image_url"]["url"],"detail":p["image_url"].get("detail") or "auto"})
            items.append({"type":"message","role":"user","content":content})
    body={"model":config.model,"input":items}
    if instructions: body["instructions"]="\n\n".join(instructions)
    if stream: body["stream"]=True
    if s.get("supports_store"): body["store"]=False
    if opts:
        if s.get("supports_temperature") and not config.reasoning:
            if opts.temperature>0: body["temperature"]=opts.temperature
            if opts.top_p>0: body["top_p"]=opts.top_p
        if opts.completion_budget()>0 and s.get("supports_max_output_tokens"):
            body["max_output_tokens"]=max(opts.completion_budget(),16)
        if opts.tools:
            body["tools"]=[{"type":t.type or "function","name":t.name,"description":t.description,
                            **({"parameters":deepcopy(t.parameters)} if t.parameters else {})} for t in opts.tools]
            if opts.parallel_tool_calls is not None and s.get("supports_parallel_tool_calls"):
                body["parallel_tool_calls"]=opts.parallel_tool_calls
            if opts.tool_choice:
                body["tool_choice"]=opts.tool_choice if opts.tool_choice in ("none","auto","required") else {"type":"function","name":opts.tool_choice}
        if opts.format:
            body["text"]={"format":{"type":"json_object"}}
            for item in reversed(items):
                if item.get("type")=="message" and item.get("role")=="user":
                    hint="\nUse this JSON schema: "+opts.format
                    if isinstance(item["content"],str): item["content"]+=hint
                    else:
                        texts=[p for p in item["content"] if p["type"]=="input_text"]
                        if texts: texts[-1]["text"]=texts[-1].get("text","")+hint
                        else: item["content"].append({"type":"input_text","text":hint.lstrip("\n")})
                    break
        if s.get("prompt_cache_key") and opts.cache_retention!="none" and opts.prompt_cache_key:
            body["prompt_cache_key"]=opts.prompt_cache_key[:64]
            if opts.cache_retention=="long" and s.get("supports_long_cache_retention"): body["prompt_cache_retention"]="24h"
    level,requested=_level(config,opts)
    if requested:
        if level not in ("","off"):
            reasoning={}
            if level in LADDER: reasoning["effort"]=_value(config,level)
            if s.get("supports_reasoning_summary"): reasoning["summary"]="auto"
            if reasoning: body["reasoning"]=reasoning
        elif config.thinking_levels.get("off"): body["reasoning"]={"effort":config.thinking_levels["off"]}
    if config.reasoning and s.get("supports_encrypted_reasoning"): body["include"]=["reasoning.encrypted_content"]
    return _extra(body,s)


def anthropic_plan(config,opts):
    s=config.protocol_settings.get("anthropic-messages",{})
    level,requested=_level(config,opts)
    if not requested: return {},"",0,False
    if level in ("","off"):
        return ({"type":"disabled"} if supports_level(config.thinking_levels,"off") and s.get("thinking_mode")=="adaptive" else {}),"",0,False
    if s.get("thinking_mode")=="adaptive":
        return {"type":"adaptive"},(_value(config,level) if level in LADDER and s.get("supports_effort") else ""),0,True
    budget=opts.thinking_budget_tokens if opts and opts.thinking_budget_tokens>0 else s.get("thinking_budgets",{}).get(level if level in LADDER else "medium",0)
    budget=max(budget,1024)
    return {"type":"enabled","budget_tokens":budget},"",budget,True


def _anthropic_image(url):
    if url.startswith("data:"):
        if ";base64," not in url: return None
        mime,data=url[5:].split(";base64,",1)
        try: base64.b64decode(data,validate=True)
        except ValueError: return None
        return {"type":"base64","media_type":mime,"data":data}
    if urlsplit(url).scheme in ("http","https"): return {"type":"url","url":url}
    return None


def anthropic_body(config,messages,opts,stream,resolve):
    s=config.protocol_settings.get(config.api,{})
    plan,effort,budget,enabled=anthropic_plan(config,opts)
    maximum=opts.completion_budget() if opts else 0
    maximum=maximum if maximum>0 else s.get("default_max_tokens",4096) or 4096
    if budget>0 and maximum<=budget: maximum=budget+max(maximum,1024)
    body={"model":config.model,"max_tokens":maximum}
    if stream: body["stream"]=True
    if opts:
        if s.get("supports_temperature") and opts.temperature>0 and (not enabled or s.get("temperature_with_thinking")):
            body["temperature"]=opts.temperature
        elif s.get("supports_top_p") and opts.top_p>0 and not enabled: body["top_p"]=opts.top_p
    marker=None
    if s.get("supports_cache_control") and (opts is None or opts.cache_retention!="none"):
        marker={"type":"ephemeral"}
        if opts and opts.cache_retention=="long" and s.get("long_cache_ttl"): marker["ttl"]=s["long_cache_ttl"]
    if opts and opts.tools:
        tools=[{"name":t.name,"description":t.description,"input_schema":deepcopy(t.parameters) or {"type":"object","properties":{}}} for t in opts.tools]
        if marker and s.get("supports_cache_control_on_tools"): tools[-1]["cache_control"]=dict(marker)
        body["tools"]=tools
        choice={"type":{"required":"any","none":"none"}.get(opts.tool_choice,"auto")}
        if opts.tool_choice not in ("","auto","required","none"): choice={"type":"tool","name":opts.tool_choice}
        if opts.parallel_tool_calls is not None and choice["type"]!="none": choice["disable_parallel_tool_use"]=not opts.parallel_tool_calls
        body["tool_choice"]=choice
    system,converted=[],[]
    for msg in messages:
        content=neutralize(msg.content).strip()
        if msg.role=="system":
            content=content or "\n".join(p.get("text","").strip() for p in _parts(msg,resolve) if p.get("type")=="text" and p.get("text","").strip())
            if content: system.append(content)
            continue
        blocks=[]
        if msg.role=="assistant":
            stored=_json(msg.reasoning_metadata.get("anthropic_thinking_blocks"),None)
            if isinstance(stored,list):
                blocks.extend(deepcopy(b) for b in stored if isinstance(b,dict) and (b.get("type")=="redacted_thinking" or b.get("type")=="thinking" and b.get("signature")))
            else:
                redacted=_json(msg.reasoning_metadata.get("anthropic_redacted_thinking",[]),[])
                if isinstance(redacted,list): blocks.extend({"type":"redacted_thinking","data":d} for d in redacted)
                prefix="anthropic-messages:"
                if msg.reasoning_content and msg.reasoning_signature.startswith(prefix):
                    blocks.append({"type":"thinking","thinking":neutralize(msg.reasoning_content),"signature":msg.reasoning_signature[len(prefix):]})
            content=content or "\n".join(p.get("text","").strip() for p in _parts(msg,resolve) if p.get("type")=="text" and p.get("text","").strip())
            if content: blocks.append({"type":"text","text":content})
            for tc in msg.tool_calls:
                blocks.append({"type":"tool_use","id":tc.id,"name":tc.function.name,"input":_json(neutralize(tc.function.arguments),{})})
            role="assistant"
        elif msg.role=="tool":
            block={"type":"tool_result","tool_use_id":msg.tool_call_id,"content":neutralize(msg.content)}
            if converted and converted[-1]["role"]=="user" and isinstance(converted[-1]["content"],list) and converted[-1]["content"] and converted[-1]["content"][0]["type"]=="tool_result":
                converted[-1]["content"].append(block); continue
            blocks,role=[block],"user"
        else:
            for p in _parts(msg,resolve):
                if p.get("type")=="text" and p.get("text","").strip(): blocks.append({"type":"text","text":p["text"].strip()})
                elif p.get("image_url"):
                    source=_anthropic_image(p["image_url"]["url"])
                    if source: blocks.append({"type":"image","source":source})
            if msg.multi_content and content: blocks.append({"type":"text","text":content})
            role="user"
        if not blocks: continue
        value=blocks[0]["text"] if len(blocks)==1 and blocks[0]["type"]=="text" else blocks
        converted.append({"role":role,"content":value})
    if system:
        body["system"]=[{"type":"text","text":"\n\n".join(system),"cache_control":dict(marker)}] if marker else "\n\n".join(system)
    if marker and converted:
        content=converted[-1]["content"]
        if isinstance(content,str) and content:
            converted[-1]["content"]=[{"type":"text","text":content,"cache_control":dict(marker)}]
        elif isinstance(content,list) and content: content[-1]["cache_control"]=dict(marker)
    body["messages"]=converted
    if plan: body["thinking"]=plan
    if effort: body["output_config"]={"effort":effort}
    return _extra(body,s)


def _google_image(url):
    if url.startswith("data:"):
        if "," not in url: return None
        header,data=url[5:].split(",",1)
        encoded=header.endswith(";base64")
        mime=header.removesuffix(";base64").split(";",1)[0] or "image/jpeg"
        if not encoded: data=base64.b64encode(unquote(data).encode()).decode()
        return {"inlineData":{"mimeType":mime,"data":data}}
    mime=mimetypes.guess_type(urlsplit(url).path)[0] or "image/jpeg"
    return {"fileData":{"mimeType":mime,"fileUri":url}}


def _sanitize_schema(value):
    unsupported={"$schema","$id","$ref","$defs","definitions","additionalProperties","default","examples","title"}
    if isinstance(value,list): return [_sanitize_schema(v) for v in value]
    if not isinstance(value,dict): return value
    return {k:({name:_sanitize_schema(sub) for name,sub in v.items()} if k=="properties" and isinstance(v,dict) else _sanitize_schema(v)) for k,v in value.items() if k not in unsupported}


def google_body(config,messages,opts,stream,resolve):
    s=config.protocol_settings.get(config.api,{})
    system,contents,tool_names,open_batch=[],[],{},-1
    for msg in messages:
        content=neutralize(msg.content)
        if msg.role=="system":
            text="\n".join(p["text"] for p in _parts(msg,resolve) if p.get("text")) if msg.multi_content else content
            if text: system.append(text)
            continue
        if msg.role=="tool":
            result=_json(content,content) if content.strip().startswith(("{","[")) else content
            response={"name":msg.name or tool_names.get(msg.tool_call_id,""),"response":{"result":result}}
            if msg.tool_call_id: response["id"]=msg.tool_call_id
            part={"functionResponse":response}
            if open_batch==len(contents)-1 and open_batch>=0: contents[open_batch]["parts"].append(part); continue
            contents.append({"role":"user","parts":[part]});open_batch=len(contents)-1;continue
        parts=[]
        if msg.role=="assistant":
            if content or not msg.tool_calls:
                p={"text":content}
                if not msg.tool_calls and msg.reasoning_signature.startswith("google-generative-ai:"): p["thoughtSignature"]=msg.reasoning_signature[len("google-generative-ai:"):]
                parts.append(p)
            for tc in msg.tool_calls:
                if tc.id: tool_names[tc.id]=tc.function.name
                args=_json(neutralize(tc.function.arguments),{})
                fc={"name":tc.function.name,"args":args if isinstance(args,dict) else {}}
                if tc.id: fc["id"]=tc.id
                p={"functionCall":fc}
                sig=_opaque_string(tc.provider_metadata.get("thought_signature"))
                if sig: p["thoughtSignature"]=sig
                parts.append(p)
            role="model"
        else:
            for p in _parts(msg,resolve):
                if p.get("type")=="text" and p.get("text"): parts.append({"text":p["text"]})
                elif p.get("image_url"):
                    img=_google_image(p["image_url"]["url"])
                    if img: parts.append(img)
            if not parts: parts=[{"text":content}]
            role="user"
        contents.append({"role":role,"parts":parts})
    body={"contents":contents}
    if system: body["systemInstruction"]={"parts":[{"text":"\n\n".join(system)}]}
    gen={}
    if opts:
        for name,wire in (("temperature","temperature"),("top_p","topP")):
            value=getattr(opts,name)
            if value>0: gen[wire]=value
        if s.get("supports_seed") and opts.seed>0: gen["seed"]=opts.seed
        if s.get("supports_penalty"):
            for name,wire in (("frequency_penalty","frequencyPenalty"),("presence_penalty","presencePenalty")):
                if getattr(opts,name)>0: gen[wire]=getattr(opts,name)
        if opts.completion_budget()>0: gen["maxOutputTokens"]=opts.completion_budget()
        if opts.tools:
            body["tools"]=[{"functionDeclarations":[{"name":t.name,"description":t.description,**({"parameters":_sanitize_schema(t.parameters)} if isinstance(t.parameters,dict) else {})} for t in opts.tools]}]
            if opts.tool_choice:
                choice={"mode":{"auto":"AUTO","required":"ANY","none":"NONE"}.get(opts.tool_choice,"ANY")}
                if opts.tool_choice not in ("auto","required","none"): choice["allowedFunctionNames"]=[opts.tool_choice]
                body["toolConfig"]={"functionCallingConfig":choice}
        if opts.format:
            gen["responseMimeType"]="application/json"
            for item in reversed(contents):
                if item["role"]=="user":
                    texts=[p for p in item["parts"] if isinstance(p.get("text"),str)]
                    hint="\nUse this JSON schema: "+opts.format
                    if texts: texts[-1]["text"]+=hint
                    else: item["parts"].append({"text":hint.lstrip("\n")})
                    break
        level,requested=_level(config,opts)
        if requested and config.reasoning:
            mode=s.get("thinking_mode") or "budget"
            thought={"includeThoughts":s.get("include_thoughts",False)}
            if mode=="budget":
                if level=="off": thought={"thinkingBudget":0} if supports_level(config.thinking_levels,"off") else None
                else: thought["thinkingBudget"]=-1 if level=="auto" else opts.thinking_budget_tokens or s.get("thinking_budgets",{}).get(level,-1)
            elif mode=="level":
                if level=="off": thought={"thinkingLevel":_value(config,"off")} if config.thinking_levels.get("off") else None
                elif level in LADDER: thought["thinkingLevel"]=_value(config,level)
            else: thought=None
            if thought: gen["thinkingConfig"]=thought
    _extra(gen,s,"extra_generation_config")
    if gen: body["generationConfig"]=gen
    return body


def build_native_body(config,messages,opts=None,*,stream=False,image_resolver=None):
    builders={"openai-responses":responses_body,"anthropic-messages":anthropic_body,"google-generative-ai":google_body}
    if config.api not in builders: raise ProviderError("MODEL_PROTOCOL_UNSUPPORTED")
    return builders[config.api](config,messages,opts,stream,image_resolver or (lambda url:url))
