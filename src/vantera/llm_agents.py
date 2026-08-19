from __future__ import annotations
import hashlib,json,os,time,urllib.error,urllib.request,uuid
from dataclasses import dataclass
from datetime import UTC,datetime,timedelta
from typing import Any,Protocol
from .db import utcnow

def ident(p): return f"{p}_{uuid.uuid4().hex[:16]}"
@dataclass
class ModelResult:
    text:str; response_id:str|None; model:str; input_tokens:int|None=None; output_tokens:int|None=None; total_tokens:int|None=None; provider:str="unknown"; latency_ms:int|None=None
class ModelProvider(Protocol):
    name:str; default_model:str
    def invoke(self,*,model:str,instructions:str,input_text:str)->ModelResult:...
class MissingModelCredential(RuntimeError): pass
class ModelQuotaExhausted(RuntimeError): pass

class GeminiProvider:
    name="gemini"; default_model="gemini-2.5-flash-lite"; root="https://generativelanguage.googleapis.com/v1beta"
    preferred=("gemini-2.5-flash-lite","gemini-2.5-flash")
    def __init__(self,api_key=None,timeout=90): self.api_key=api_key if api_key is not None else os.getenv("GEMINI_API_KEY"); self.timeout=timeout; self._model=None
    def _request(self,url,body=None):
        if not self.api_key: raise MissingModelCredential("GEMINI_API_KEY is not configured; no model invocation occurred")
        req=urllib.request.Request(url,data=body,method="POST" if body else "GET",headers={"x-goog-api-key":self.api_key,"Content-Type":"application/json","User-Agent":"VANTERA/2.0"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as r:return json.load(r)
        except urllib.error.HTTPError as e:
            detail=e.read().decode("utf-8","replace")[:1000]
            if e.code==429 or "RESOURCE_EXHAUSTED" in detail: raise ModelQuotaExhausted("Gemini free-tier quota is temporarily exhausted") from e
            raise RuntimeError(f"Gemini API HTTP {e.code}: {detail}") from e
    def resolve_model(self,requested=None):
        if self._model:return self._model
        data=self._request(f"{self.root}/models?pageSize=100")
        available={m.get("name","").removeprefix("models/") for m in data.get("models",[]) if "generateContent" in m.get("supportedGenerationMethods",[])}
        candidates=([requested] if requested and requested.startswith("gemini-") else [])+list(self.preferred)
        self._model=next((m for m in candidates if m in available),None)
        if not self._model:raise RuntimeError("No supported stable Gemini Flash model is available for this API project")
        return self._model
    def invoke(self,*,model,instructions,input_text):
        started=time.monotonic(); resolved=self.resolve_model(model)
        body=json.dumps({"system_instruction":{"parts":[{"text":instructions}]},"contents":[{"role":"user","parts":[{"text":input_text}]}],"generationConfig":{"maxOutputTokens":1200,"responseMimeType":"text/plain"}}).encode()
        p=self._request(f"{self.root}/models/{resolved}:generateContent",body)
        text="".join(x.get("text","") for c in p.get("candidates",[]) for x in c.get("content",{}).get("parts",[])).strip()
        if not text:raise RuntimeError("Gemini returned no text output")
        u=p.get("usageMetadata") or {}
        return ModelResult(text,p.get("responseId"),p.get("modelVersion",resolved),u.get("promptTokenCount"),u.get("candidatesTokenCount"),u.get("totalTokenCount"),self.name,int((time.monotonic()-started)*1000))

class OpenAIResponsesProvider:
    name="openai";default_model="gpt-5-mini";endpoint="https://api.openai.com/v1/responses"
    def __init__(self,api_key=None,timeout=90):self.api_key=api_key if api_key is not None else os.getenv("OPENAI_API_KEY");self.timeout=timeout
    def invoke(self,*,model,instructions,input_text):
        if not self.api_key:raise MissingModelCredential("OPENAI_API_KEY is not configured; no model invocation occurred")
        started=time.monotonic();body=json.dumps({"model":model,"instructions":instructions,"input":input_text,"store":False,"max_output_tokens":1200}).encode()
        req=urllib.request.Request(self.endpoint,data=body,method="POST",headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as r:p=json.load(r)
        except urllib.error.HTTPError as e:
            if e.code==429:raise ModelQuotaExhausted("OpenAI quota is temporarily exhausted") from e
            raise
        text=p.get("output_text") or "".join(c.get("text","") for i in p.get("output",[]) for c in i.get("content",[]) if c.get("type")=="output_text");u=p.get("usage") or {}
        return ModelResult(text.strip(),p.get("id"),p.get("model",model),u.get("input_tokens"),u.get("output_tokens"),u.get("total_tokens"),self.name,int((time.monotonic()-started)*1000))
class BlockedProvider:
    name="none";default_model="none"
    def invoke(self,**kwargs):raise MissingModelCredential("No genuine model provider is authenticated; no model invocation occurred")
def select_provider():
    if os.getenv("GEMINI_API_KEY"):return GeminiProvider()
    if os.getenv("OPENAI_API_KEY"):return OpenAIResponsesProvider()
    return BlockedProvider()

class ExecutiveRuntime:
    def __init__(self,db,provider=None,model=None,cycle_limit=None):
        self.db=db;self.provider=provider or select_provider();self.provider_name=getattr(self.provider,"name","test");self.model=model or os.getenv("VANTERA_AGENT_MODEL") or getattr(self.provider,"default_model","test-model");self.cycle_limit=cycle_limit or int(os.getenv("VANTERA_MODEL_CYCLE_LIMIT","10"));self.cycle_calls=0
    def context(self,agent_id):
        p=self.db.one("SELECT * FROM agent_profiles WHERE agent_id=?",(agent_id,)) or {}
        facts={"identity":{k:p.get(k) for k in ("full_name","title","department","biography","decision_style")},"memories":self.db.query("SELECT memory_type,subject,content FROM agent_memories WHERE agent_id=? ORDER BY importance DESC,created_at DESC LIMIT 8",(agent_id,)),"owner_directives":self.db.query("SELECT content,status FROM owner_directives WHERE status!='REVOKED' ORDER BY created_at DESC LIMIT 8"),"business_units":self.db.query("SELECT name,status,thesis,target_customer,monetization_model,revenue_cents,expense_cents FROM business_units ORDER BY updated_at DESC LIMIT 25"),"policy":{"autonomous_spend_limit_eur":0,"revenue_requires_external_evidence":True,"owner_is_non_operational":True}}
        return json.dumps(facts,ensure_ascii=False)[:16000]
    def _allowed(self,agent_id):
        day=datetime.now(UTC).date().isoformat();p=self.provider_name
        total=self.db.one("SELECT request_count FROM model_daily_usage WHERE usage_date=? AND provider=?",(day,p)) or {};agent=self.db.one("SELECT COUNT(*) n FROM model_runs WHERE agent_id=? AND provider=? AND started_at LIKE ?",(agent_id,p,day+"%")) or {}
        return total.get("request_count",0)<int(os.getenv("VANTERA_MODEL_DAILY_LIMIT","40")) and agent.get("n",0)<int(os.getenv("VANTERA_MODEL_AGENT_DAILY_LIMIT","6"))
    def _queue(self,a,p,t,e,tools,d,error):
        key=hashlib.sha256(f"{a}|{p}|{t}".encode()).hexdigest();old=self.db.one("SELECT attempts FROM model_work_queue WHERE dedupe_key=?",(key,)) or {};attempts=old.get("attempts",0)+1;now=utcnow();nxt=(datetime.now(UTC)+timedelta(minutes=min(360,2**min(attempts,8)))).isoformat()
        with self.db.connect() as c:c.execute("INSERT INTO model_work_queue(id,dedupe_key,agent_id,purpose,task,evidence_refs_json,tools_json,delegations_json,status,attempts,next_attempt_at,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO UPDATE SET status='WAITING_FOR_MODEL_QUOTA',attempts=?,next_attempt_at=?,last_error=?,updated_at=?",(ident("work"),key,a,p,t,json.dumps(e),json.dumps(tools),json.dumps(d),"WAITING_FOR_MODEL_QUOTA",attempts,nxt,error,now,now,attempts,nxt,error,now))
    def run(self,agent_id,purpose,task,*,evidence_refs=None,tools=None,delegations=None):
        evidence_refs=evidence_refs or [];tools=tools or [];delegations=delegations or [];run_id=ident("run");started=utcnow();p=self.db.one("SELECT full_name,title,responsibilities_json,authority_limits_json FROM agent_profiles WHERE agent_id=?",(agent_id,))
        if not p:raise ValueError(f"Unknown persistent executive {agent_id}")
        instructions=f"You are {p['full_name']}, {p['title']} of VANTERA. Use verified context. Never invent external execution or money. Return concise executive output and observable rationale, never hidden reasoning. Responsibilities: {p['responsibilities_json']}. Limits: {p['authority_limits_json']}.";input_text=f"PURPOSE\n{purpose}\n\nTASK\n{task}\n\nLIVE CONTEXT\n{self.context(agent_id)}"
        with self.db.connect() as c:c.execute("INSERT INTO model_runs(id,agent_id,model,purpose,input_summary,evidence_refs_json,tools_json,delegations_json,status,started_at,provider,request_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(run_id,agent_id,self.model,purpose,input_text[:4000],json.dumps(evidence_refs),json.dumps(tools),json.dumps(delegations),"RUNNING",started,self.provider_name,"REQUESTING"))
        try:
            if self.cycle_calls>=self.cycle_limit or not self._allowed(agent_id):raise ModelQuotaExhausted("Configured model request budget reached")
            self.cycle_calls+=1;r=self.provider.invoke(model=self.model,instructions=instructions,input_text=input_text);summary=r.text[:600];day=datetime.now(UTC).date().isoformat()
            with self.db.connect() as c:
                c.execute("UPDATE model_runs SET model=?,provider=?,output_text=?,decision_summary=?,status='COMPLETED',request_status='SUCCESS',provider_response_id=?,input_tokens=?,output_tokens=?,total_tokens=?,latency_ms=?,completed_at=? WHERE id=?",(r.model,r.provider,r.text,summary,r.response_id,r.input_tokens,r.output_tokens,r.total_tokens,r.latency_ms,utcnow(),run_id))
                c.execute("INSERT INTO model_daily_usage VALUES(?,?,?,?,?,?) ON CONFLICT(usage_date,provider) DO UPDATE SET request_count=request_count+1,input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens,updated_at=excluded.updated_at",(day,r.provider,1,r.input_tokens or 0,r.output_tokens or 0,utcnow()))
                c.execute("INSERT INTO agent_memories VALUES(?,?,?,?,?,?,?,?,?)",(ident("mem"),agent_id,"decision",purpose,summary,.7,run_id,utcnow(),None))
            self.db.event("real_agent_completed",agent_id,"VERIFIED RESULT",{"provider":r.provider,"model":r.model,"run_id":run_id,"summary":summary});return {"run_id":run_id,"status":"COMPLETED","output":r.text,"model":r.model,"provider":r.provider}
        except ModelQuotaExhausted as x:
            self._queue(agent_id,purpose,task,evidence_refs,tools,delegations,str(x))
            with self.db.connect() as c:c.execute("UPDATE model_runs SET status='WAITING_FOR_MODEL_QUOTA',request_status='DEFERRED',error=?,completed_at=? WHERE id=?",(str(x),utcnow(),run_id))
            return {"run_id":run_id,"status":"WAITING_FOR_MODEL_QUOTA","error":str(x),"model":self.model,"provider":self.provider_name}
        except Exception as x:
            with self.db.connect() as c:c.execute("UPDATE model_runs SET status='BLOCKED',request_status='FAILED',error=?,completed_at=? WHERE id=?",(str(x),utcnow(),run_id))
            return {"run_id":run_id,"status":"BLOCKED","error":str(x),"model":self.model,"provider":self.provider_name}
    def delegate(self,sender,recipient,content,*,business_unit_id=None):
        mid=ident("msg")
        with self.db.connect() as c:c.execute("INSERT INTO agent_messages VALUES(?,?,?,?,?,?,?,?,?)",(mid,ident("conv"),sender,recipient,"DELEGATION",content,None,business_unit_id,utcnow()))
        self.db.event("agent_delegated",sender,"EXECUTED",{"recipient":recipient,"summary":content[:300]});return mid
    def ceo_chat(self,message,conversation_id="owner-ceo"):
        oid=ident("chat")
        with self.db.connect() as c:c.execute("INSERT INTO ceo_chat_messages VALUES(?,?,?,?,?,?,?)",(oid,conversation_id,"owner",message,None,"RECEIVED",utcnow()));c.execute("INSERT INTO owner_directives VALUES(?,?,?,?,?,?)",(ident("directive"),message,"PENDING_INTERPRETATION",None,utcnow(),None))
        r=self.run("ceo","Owner conversation and strategic instruction",message,tools=["company_state","portfolio","verified_finance"])
        if r["status"]!="COMPLETED":return {"status":r["status"],"message_id":oid,"error":r["error"]}
        rid=ident("chat")
        with self.db.connect() as c:c.execute("INSERT INTO ceo_chat_messages VALUES(?,?,?,?,?,?,?)",(rid,conversation_id,"ceo",r["output"],r["run_id"],"ANSWERED",utcnow()))
        return {"status":"ANSWERED","message_id":rid,"response":r["output"],"model_run_id":r["run_id"]}

EXECUTIVE_REVIEW_ORDER=("cvo","cso","cto","cmo","sales","cfo","coo")
def run_multi_agent_review(db,prompt,provider=None):
    rt=ExecutiveRuntime(db,provider);briefs=[];previous=""
    for a in EXECUTIVE_REVIEW_ORDER:
        rt.delegate("ceo" if not briefs else EXECUTIVE_REVIEW_ORDER[len(briefs)-1],a,f"Prepare your executive brief for: {prompt}");r=rt.run(a,"Executive commercial review",prompt+"\nPrevious brief:\n"+previous[:1200],delegations=[a]);briefs.append({"agent_id":a,**r})
        if r["status"]!="COMPLETED":return {"status":r["status"],"briefs":briefs,"reason":r.get("error")}
        previous=r["output"]
    d=rt.run("ceo","Final portfolio decision",prompt+"\nExecutive briefs:\n"+json.dumps(briefs),delegations=list(EXECUTIVE_REVIEW_ORDER));return {"status":d["status"],"briefs":briefs,"decision":d}
def resume_model_queue(db,limit=4):
    rows=db.query("SELECT * FROM model_work_queue WHERE status='WAITING_FOR_MODEL_QUOTA' AND next_attempt_at<=? ORDER BY created_at LIMIT ?",(utcnow(),limit));rt=ExecutiveRuntime(db,cycle_limit=limit);out=[]
    for x in rows:
        r=rt.run(x["agent_id"],x["purpose"],x["task"],evidence_refs=json.loads(x["evidence_refs_json"]),tools=json.loads(x["tools_json"]),delegations=json.loads(x["delegations_json"]));out.append(r)
        if r["status"]=="COMPLETED":
            with db.connect() as c:c.execute("UPDATE model_work_queue SET status='COMPLETED',updated_at=? WHERE id=?",(utcnow(),x["id"]))
    return out
