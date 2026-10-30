"""Fail-closed data boundary. No card is authenticated or used in routing in v0.6.9."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib, json, re
from jsonschema import Draft202012Validator

@dataclass
class ExperienceSourcePolicy:
    local_live_enabled: bool = True
    community_enabled: bool = False
    def validate(self):
        if type(self.local_live_enabled) is not bool or type(self.community_enabled) is not bool:
            raise ValueError('Experience source switches must be booleans.')

class TrustState(StrEnum):
    UNVERIFIED='UNVERIFIED'
    SIGNATURE_VALID='SIGNATURE_VALID'
    SIGNATURE_INVALID='SIGNATURE_INVALID'
    FINGERPRINT_MISMATCH='FINGERPRINT_MISMATCH'
    STALE='STALE'
    UNSUPPORTED_SCHEMA='UNSUPPORTED_SCHEMA'
    TRUSTED='TRUSTED'

identifier={'type':'string','minLength':1,'maxLength':120,'pattern':r'^[A-Za-z0-9_.:@+-]+$'}
hex64={'type':'string','pattern':'^[a-f0-9]{64}$'}
count={'type':'integer','minimum':0,'maximum':10000000}
summary={'type':'object','additionalProperties':False,'required':['median','unit'],'properties':{'median':{'type':'number','minimum':0,'maximum':1000000000000},'unit':{'enum':['ms','bytes','tokens','watts','joules']}}}
CARD_SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'AlienApe Community Model Card V1 (draft; unverified)',
 'type':'object','additionalProperties':False,
 'required':['schema_version','card_id','model_fingerprint','model_name','model_digest','quantization','backend_identity','capability_v2','protocol_v2','language_v2','sample_count','verified_successes','verified_failures','unknown_results','created_at','stale_after','issuer_id','payload_hash','signature_scheme','signature'],
 'properties':{'schema_version':{'const':'1.0.0'},'card_id':identifier,'model_fingerprint':hex64,'model_name':identifier,
  'model_digest':hex64,'quantization':identifier,'backend_identity':identifier,'capability_v2':{'enum':['general_chat','explanation','summarization','coding','code_generation','code_debugging','code_review','reasoning','tool_selection','information_synthesis','long_context_analysis']},'protocol_v2':{'enum':['plain_text','native_tool_calling','json_fallback','structured_json','file_proposal','analysis_result','tool_action']},'language_v2':{'type':'string','pattern':'^[a-z]{2,3}$'},
  'sample_count':count,'verified_successes':count,'verified_failures':count,'unknown_results':count,
  'latency_summary':summary,'resource_summary':summary,'benchmark_catalog_version':identifier,
  'created_at':{'type':'string','maxLength':32},'stale_after':{'type':'string','maxLength':32},'issuer_id':identifier,'payload_hash':hex64,
  'signature_scheme':{'enum':['none','ed25519']},'signature':{'type':'string','maxLength':256,'pattern':'^[A-Za-z0-9+/=_-]*$'}}}

def canonical_payload(card):
    """UTF-8 JSON, sorted keys, compact separators, no NaN; omit hash and signature."""
    return json.dumps({k:v for k,v in card.items() if k not in {'payload_hash','signature'}},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def card_state(card, fingerprint, now=None):
    try:
        if not isinstance(card,dict):return TrustState.UNVERIFIED
        if card.get('schema_version')!='1.0.0':return TrustState.UNSUPPORTED_SCHEMA
        if list(Draft202012Validator(CARD_SCHEMA).iter_errors(card)):return TrustState.UNVERIFIED
        if hashlib.sha256(canonical_payload(card)).hexdigest()!=card['payload_hash']:return TrustState.UNVERIFIED
        if card['verified_successes']+card['verified_failures']+card['unknown_results']!=card['sample_count']:return TrustState.UNVERIFIED
        created=datetime.fromisoformat(card['created_at'].replace('Z','+00:00'))
        stale=datetime.fromisoformat(card['stale_after'].replace('Z','+00:00'))
        if created.tzinfo is None or stale.tzinfo is None or stale<=created:return TrustState.UNVERIFIED
        if stale <= (now or datetime.now(timezone.utc)):return TrustState.STALE
        if not isinstance(fingerprint,str) or not re.fullmatch('[a-f0-9]{64}',fingerprint) or card['model_fingerprint']!=fingerprint:return TrustState.FINGERPRINT_MISMATCH
        return TrustState.UNVERIFIED
    except (ValueError,TypeError,OverflowError):return TrustState.UNVERIFIED

def community_evidence_for_routing(*args, **kwargs):
    # No approved verifier or provider exists in this release, including when enabled.
    return ()
