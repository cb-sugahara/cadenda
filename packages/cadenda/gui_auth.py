"""Process-local owner pairing. No existing OS/account credential is collected."""
import hashlib
import secrets
import time
import threading
from functools import wraps
from dataclasses import dataclass

from .errors import CadendaError


def synchronized(fn):
    @wraps(fn)
    def call(self,*args,**kwargs):
        with self.lock:return fn(self,*args,**kwargs)
    return call


@dataclass
class Session:
    actor: str
    csrf: str
    expires: float


class OwnerAuth:
    def __init__(self, pairing_code):
        self.lock=threading.RLock()
        self.pairing_hash=self.hash(pairing_code);self.sessions={};self.failures=[];self.challenges={}

    @staticmethod
    def hash(value):return hashlib.sha256(value.encode()).hexdigest()

    @synchronized
    def login(self, code):
        now=time.time();self.failures=[t for t in self.failures if t>now-60]
        if len(self.failures)>=5:raise CadendaError('LOGIN_RATE_LIMIT','Wait one minute before trying again')
        if not self.pairing_hash or not secrets.compare_digest(self.hash(code),self.pairing_hash):
            self.failures.append(now);raise CadendaError('UNAUTHENTICATED','Pairing code is invalid or already used')
        token=secrets.token_urlsafe(32);self.pairing_hash=None
        self.sessions[self.hash(token)]=Session('owner:'+secrets.token_hex(12),secrets.token_urlsafe(32),now+8*3600)
        return token,self.sessions[self.hash(token)]

    @synchronized
    def session(self, token):
        s=self.sessions.get(self.hash(token or ''))
        if not s or s.expires<=time.time():raise CadendaError('UNAUTHENTICATED','Pair with this Mac to continue')
        return s

    @synchronized
    def challenge(self, session, target, fingerprint):
        now=time.time();self.challenges={k:v for k,v in self.challenges.items() if v['expires']>now}
        key=secrets.token_urlsafe(24);self.challenges[key]={'actor':session.actor,'target':target,'fingerprint':fingerprint,'expires':now+300}
        return {'challenge':key,'fingerprint':fingerprint,'expires_at':now+300}

    @synchronized
    def consume(self, session, key, target, fingerprint):
        row=self.challenges.pop(key,None)
        if not row or row['actor']!=session.actor or row['target']!=target or row['fingerprint']!=fingerprint or row['expires']<=time.time():
            raise CadendaError('STALE_CONFIRMATION','Preview the current target before confirming')

    @synchronized
    def logout(self,token):self.sessions.pop(self.hash(token or ""),None)
