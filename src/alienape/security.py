"""Deterministic controller boundaries. No model-based security decisions."""
import hashlib
import logging
import os
from pathlib import PurePosixPath
import re
import threading
import uuid
import sqlite3

REDACTED = '<REDACTED>'
_KEY = re.compile(r'^(?:authorization|proxy_authorization|api_?key|(?:access_|refresh_|session_|oauth_)?token|client_secret|password|passwd|secret|private_key)$', re.I)
def secret_key(key):
    key = str(key).casefold().replace('-', '_')
    return bool(_KEY.fullmatch(key) or key.endswith(('_api_key','_access_token','_refresh_token','_client_secret','_password','_secret')))

class SecretRedactor:
    def __init__(self):
        self._values=set();self._lock=threading.RLock()
    def register(self,value):
        if isinstance(value,str) and value and value!=REDACTED:
            with self._lock:self._values.add(value)
        return value
    def text(self,value):
        text=str(value)
        with self._lock:
            for item in sorted(self._values,key=len,reverse=True):text=text.replace(item,REDACTED)
        text=re.sub(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END (?:[A-Z ]*PRIVATE KEY)-----',REDACTED,text)
        text=re.sub(r'(?i)\b((?:proxy-)?authorization\s*[=:]\s*[\x22\x27]?)(?:Bearer\s+|Basic\s+)?[^\s,;\x22\x27}]+',lambda m:m[1]+REDACTED,text)
        # Anchored key/value names, not token_count or password_validator.
        keys=r'(?:[\w-]*api[_-]?key|(?:access_|refresh_|session_|oauth_)?token|client_secret|password|passwd|secret)'
        pattern=r'(?i)(\b'+keys+r'[\x22\x27]?\s*(?:=|:|\bist\b|\bis\b)\s*)([\x22\x27])([^\r\n]*?)\2'
        text=re.sub(pattern,lambda m:m[1]+m[2]+REDACTED+m[2],text)
        text=re.sub(r'(?i)(\b'+keys+r'[\x22\x27]?\s*(?:=|:|\bist\b|\bis\b)\s*)(?![\x22\x27])([^\s&;,}\x22\x27]+)',lambda m:m[1]+REDACTED,text)
        text=re.sub(r'(?i)(\b(?:api[-_ ]?key|passwort|password|access[-_ ]?token|secret)\s+)([A-Za-z0-9_./+\-=]{6,})',lambda m:m[1]+REDACTED,text)
        return text
    def sensitive_memory(self,value):
        """Detect obvious credentials without logging them; register matched values for redaction."""
        if not isinstance(value,str):return True
        marker=r'(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|bearer|password|passwort|passwd|private[ _-]?key|credential|secret|zugangsdaten)'
        sensitive=bool(re.search(r'(?i)\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|bearer|password|passwort|passwd|private[ _-]?key|credential|zugangsdaten)\b',value) or re.search(r'(?i)\bsecret\s*(?:=|:|ist|is)\s*\S+',value) or re.search(r'(?i)\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}',value) or re.search(r'-----BEGIN [A-Z ]*PRIVATE KEY-----',value) or transport_redactor.text(value)!=value)
        if sensitive:
            pattern=r'(?i)\b'+marker+r'[\"\x27]?[ \t]*(?:(?:=|:|ist|is)\s*|\s+)(?:Bearer\s+)?(?:\"([^\"\r\n]+)\"|\x27([^\x27\r\n]+)\x27|([^\s,;}\"\x27]+))'
            for match in re.finditer(pattern,value):register_provider_secret(next((x for x in match.groups() if x),''))
            for match in re.finditer(r'(?i)\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}',value):register_provider_secret(match.group())
            for match in re.finditer(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----',value):register_provider_secret(match.group())
        return sensitive

    def clean(self,value):
        if isinstance(value,dict):return {self.text(k):REDACTED if secret_key(k) else self.clean(v) for k,v in value.items()}
        if isinstance(value,list):return [self.clean(v) for v in value]
        if isinstance(value,tuple):return tuple(self.clean(v) for v in value)
        if isinstance(value,str):return self.text(value)
        return value
    def sensitive_read(self,text):
        # Explicit sensitive reads reveal structure only, including unknown env names.
        out=[]
        for line in text.splitlines():
            if not line.strip():out.append('');continue
            if line.lstrip().startswith('#'):out.append('# '+REDACTED);continue
            match=re.match(r'(\s*(?:export\s+)?[\w.-]+\s*[=:]\s*).*',line)
            out.append(match[1]+REDACTED if match else REDACTED)
        return '\n'.join(out)

redactor=SecretRedactor()
# Transport has its own dynamic registry: diagnostic-only values cannot mutate requests.
transport_redactor=SecretRedactor()
def register_provider_secret(value):
    redactor.register(value);transport_redactor.register(value);return value

class SafeConnection(sqlite3.Connection):
    def execute(self,sql,parameters=()):return super().execute(sql,redactor.clean(parameters))
    def executemany(self,sql,parameters):return super().executemany(sql,(redactor.clean(p) for p in parameters))
def secret_environment_key(name):
    return name.upper().startswith('ALIENAPE_') or secret_key(name) or bool(re.search(r'(?:API_KEY|ACCESS_KEY|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|CREDENTIAL|SECRET)(?:$|_)',name,re.I))
for _name,_value in os.environ.items():
    if secret_environment_key(_name) and any(x in _name.upper() for x in ('KEY','TOKEN','PASSWORD','SECRET','CREDENTIAL')):register_provider_secret(_value)

def verifier_environment(environment=None):
    source=os.environ if environment is None else environment
    return {k:v for k,v in source.items() if not secret_environment_key(k) and redactor.text(v)==v}

class SensitiveFilePolicy:
    @staticmethod
    def sensitive(path):
        parts=PurePosixPath(str(path).replace('\\','/')).parts
        names=[p.casefold() for p in parts];name=names[-1] if names else ''
        if any(p in {'.ssh','.gnupg','.aws','.azure','.kube'} for p in names):return True
        if name=='.env' or name.startswith('.env.') or name in {'id_rsa','id_ed25519','id_dsa','.netrc','.npmrc','.pypirc'}:return True
        if PurePosixPath(name).suffix in {'.pem','.key','.p12','.pfx'}:return True
        if name.startswith(('credentials','secrets')) and PurePosixPath(name).suffix not in {'.py','.gd','.js','.ts','.cs','.cpp','.h','.rs','.go'}:return True
        return False
    @staticmethod
    def writable(path):
        if SensitiveFilePolicy.sensitive(path):raise PermissionError('SECRET_POLICY_BLOCK: sensible Dateien sind für Schreibtools gesperrt.')

class SafeFormatter(logging.Formatter):
    def format(self,record):return redactor.text(super().format(record))

def binding(provider,url):
    from urllib.parse import urlsplit,urlunsplit
    parsed=urlsplit(url);path=parsed.path.rstrip('/')
    if path in {'','/v1'}:path='/v1'
    canonical=urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),path,'',''))
    return hashlib.sha256((provider+'\n'+canonical).encode()).hexdigest()

def validate_reference(reference):
    if reference and not re.fullmatch(r'(?:env:[A-Za-z_][A-Za-z0-9_]*|wincred:[a-f0-9]{64}:[a-f0-9]{32})',reference):
        raise ValueError('Secret-Referenz muss env:NAME oder eine OS-Store-Referenz sein; kein Klartext-Key.')
    if reference.startswith('env:'):register_provider_secret(os.environ.get(reference[4:],''))

class WindowsCredentialStore:
    """Windows Credential Manager, user-scoped generic credentials; no custom crypto."""
    @staticmethod
    def _api():
        if os.name!='nt':raise ValueError('OS Secret Store nicht verfügbar; explizite env:-Referenz verwenden.')
        import ctypes as c
        from ctypes import wintypes as w
        class Credential(c.Structure):
            _fields_=[('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),('Comment',w.LPWSTR),('LastWritten',w.FILETIME),('CredentialBlobSize',w.DWORD),('CredentialBlob',c.POINTER(c.c_ubyte)),('Persist',w.DWORD),('AttributeCount',w.DWORD),('Attributes',c.c_void_p),('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]
        api=c.WinDLL('Advapi32.dll',use_last_error=True)
        api.CredWriteW.argtypes=[c.POINTER(Credential),w.DWORD];api.CredWriteW.restype=w.BOOL
        api.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(c.POINTER(Credential))];api.CredReadW.restype=w.BOOL
        api.CredFree.argtypes=[c.c_void_p]
        return c,Credential,api
    def write(self,reference,value):
        c,C,api=self._api();raw=value.encode('utf-16-le')
        if not raw or len(raw)>2560:raise ValueError('Credential leer oder zu lang.')
        blob=(c.c_ubyte*len(raw)).from_buffer_copy(raw);credential=C();credential.Type=1;credential.TargetName='AlienApe/'+reference;credential.CredentialBlobSize=len(raw);credential.CredentialBlob=blob;credential.Persist=2;credential.UserName='AlienApe'
        if not api.CredWriteW(c.byref(credential),0):raise ValueError('OS Credential konnte nicht gespeichert werden (Windows '+str(c.get_last_error())+'). env:-Referenz verwenden; Config bleibt unverändert.')
    def read(self,reference):
        c,C,api=self._api();pointer=c.POINTER(C)()
        if not api.CredReadW('AlienApe/'+reference,1,0,c.byref(pointer)):raise ValueError('OS Credential nicht verfügbar.')
        try:return c.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-16-le')
        finally:api.CredFree(pointer)
    def delete(self,reference):
        c,C,api=self._api()
        from ctypes import wintypes as w
        api.CredDeleteW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD];api.CredDeleteW.restype=w.BOOL
        if not reference.startswith('wincred:'):raise ValueError('Ungültige AlienApe-Referenz.')
        if not api.CredDeleteW('AlienApe/'+reference,1,0):raise ValueError('OS Credential konnte nicht entfernt werden.')

def save_secret(provider,url,value,store=None):
    register_provider_secret(value)
    reference='wincred:'+binding(provider,url)+':'+uuid.uuid4().hex
    store=store or WindowsCredentialStore();store.write(reference,value)
    if store.read(reference)!=value:raise ValueError('Credential-Bestätigung fehlgeschlagen; Config bleibt unverändert.')
    return reference

def resolve_secret(provider,url,reference='',store=None):
    if not reference:
        # Compatibility uses provider-specific env only. A shared legacy key needs explicit binding.
        value=os.environ.get('ALIENAPE_'+provider.upper()+'_API_KEY','')
    elif reference.startswith('env:'):
        name=reference[4:]
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):raise ValueError('Ungültige Secret-Referenz.')
        value=os.environ.get(name,'')
        if not value:raise ValueError('Konfigurierte Secret-Umgebung fehlt.')
    elif reference.startswith('wincred:'+binding(provider,url)+':'):
        value=(store or WindowsCredentialStore()).read(reference)
    else:raise ValueError('Secret-Referenz gehört nicht zu diesem Provider/Endpoint.')
    return register_provider_secret(value)

def install_crash_redaction():
    """Sanitize records before any handler and the top-level traceback."""
    import sys,traceback
    old=logging.getLogRecordFactory()
    if not getattr(old,'_alienape_safe',False):
        def factory(*args,**kwargs):
            record=old(*args,**kwargs)
            record.msg=redactor.text(record.getMessage());record.args=()
            if record.exc_info:
                record.exc_text=redactor.text(''.join(traceback.format_exception(*record.exc_info)));record.exc_info=None
            return record
        factory._alienape_safe=True
        logging.setLogRecordFactory(factory)
    def hook(kind,value,tb):sys.stderr.write(redactor.text(''.join(traceback.format_exception(kind,value,tb))))
    sys.excepthook=hook

def export_debug_bundle(destination,*,config=None,events=None,experience=None,memory=None):
    """Structured, local-only export. Never copies raw files or dumps environment."""
    import json
    from zipfile import ZipFile,ZIP_DEFLATED
    with ZipFile(destination,'w',ZIP_DEFLATED) as archive:
        for name,value in [('config',config),('events',events),('experience',experience),('memory',memory)]:
            if value is not None:archive.writestr(name+'.json',json.dumps(redactor.clean(value),ensure_ascii=False,default=str))
