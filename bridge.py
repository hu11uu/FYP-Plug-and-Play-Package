"""Compatibility layer that lets the original Streamlit code run outside Streamlit.

install() checks the algorithm files against source-manifest.json, then
registers small stand-ins for streamlit, pymongo, langchain_core and dotenv
so the original imports work unchanged. Use it only in this package's own
process, not inside a real Streamlit or MongoDB application.
"""
import contextlib
import contextvars
import copy
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import types

ROOT = Path(__file__).resolve().parent
CURRENT = contextvars.ContextVar("legacy_session")


def verify_source():
    manifest = json.loads((ROOT / "source-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((ROOT / "vendor" / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Frozen source integrity failure: {name}")
    return len(manifest["files"])


class State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class StateProxy:
    def __getattr__(self, key): return getattr(CURRENT.get(), key)
    def __setattr__(self, key, value): setattr(CURRENT.get(), key, value)
    def __getitem__(self, key): return CURRENT.get()[key]
    def __setitem__(self, key, value): CURRENT.get()[key] = value
    def __contains__(self, key): return key in CURRENT.get()


def cache_data(ttl=300, **_):
    # Legacy search_all has no arguments and a process-wide five-minute cache.
    # One immutable question bank per process preserves that scope here.
    def decorate(fn):
        cached = {}
        @functools.wraps(fn)
        def call(*args, **kwargs):
            key = repr((args, kwargs))
            entry = cached.get(key)
            if entry is None or time.monotonic() - entry[0] >= ttl:
                entry = (time.monotonic(), fn(*args, **kwargs))
                cached[key] = entry
            return copy.deepcopy(entry[1])
        call.clear = cached.clear
        return call
    return decorate


class HumanMessage:
    type = "human"
    def __init__(self, content): self.content = content
    def __repr__(self):
        return f"{type(self).__name__}(content={self.content!r}, additional_kwargs={{}}, response_metadata={{}})"


class AIMessage(HumanMessage):
    type = "ai"


class Store:
    """Small MongoDB-style store over SQLite, with only the methods the original code uses."""
    def __init__(self, path):
        self.path = str(path)
        self.fail_writes = False
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (collection TEXT, key TEXT, body TEXT, PRIMARY KEY(collection,key))")
    def __getitem__(self, name): return Database(self, name)
    def close(self): pass  # Connections are scoped to individual operations.


class Database:
    def __init__(self, store, name): self.store, self.name = store, name
    def __getitem__(self, name): return Collection(self.store, self.name + "." + name)


class Collection:
    def __init__(self, store, name): self.store, self.name = store, name
    def find(self, query):
        with sqlite3.connect(self.store.path) as db:
            docs = [json.loads(row[0]) for row in db.execute("SELECT body FROM documents WHERE collection=?", (self.name,))]
        return [doc for doc in docs if all(doc.get(k) == v for k, v in query.items())]
    def find_one(self, query): return next(iter(self.find(query)), None)
    def update_one(self, query, update, upsert=False):
        if self.store.fail_writes: raise OSError("Injected local storage failure")
        with sqlite3.connect(self.store.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT key,body FROM documents WHERE collection=?", (self.name,)).fetchall()
            match = next(((k, json.loads(v)) for k, v in rows if all(json.loads(v).get(q) == x for q, x in query.items())), None)
            if match is None and not upsert: return
            key, doc = match or (json.dumps(query, sort_keys=True), dict(query))
            doc.update(update.get("$set", {}))
            db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?)", (self.name, key, json.dumps(doc)))



# Common words are dropped from the query before scoring. Without this, an
# unrelated question could match a topic through a word like "of" and, in
# offline mode, get that topic's content instead of the out-of-scope reply.
_STOPWORDS = frozenset("""
    a an the of in on at to for with what who whom when where why how and
    or but do does did is are was were this that these those my your our
    their it its be as by from if then so than too very can could should
    would will just about into out up down over under again further once
    here there all any both each few more most other some such no nor not
    only own same
""".split())


class Search:
    def __init__(self, documents, all_items=False):
        self.documents, self.all_items = documents, all_items
    def search(self, search_text, top=10, **_):
        if search_text == "*" or self.all_items:
            return copy.deepcopy(self.documents[:top])
        words = set(re.findall(r"[a-z0-9]+", search_text.lower())) - _STOPWORDS
        scored = []
        for doc in self.documents:
            terms = set(re.findall(r"[a-z0-9]+", " ".join(doc.get("keywords", [])).lower()))
            score = len(words & terms)
            if score: scored.append((score, doc))
        return [copy.deepcopy(d) for _, d in sorted(scored, key=lambda x: -x[0])[:top]]


def install():
    verify_source()
    for name in ("streamlit", "pymongo", "dotenv", "langchain_core"):
        if name in sys.modules:
            raise RuntimeError("Run this isolated host in a fresh Python process; dependency already loaded: " + name)
    st = types.ModuleType("streamlit")
    st.session_state = StateProxy()
    st.spinner = lambda *a, **k: contextlib.nullcontext()
    st.cache_data = cache_data
    sys.modules["streamlit"] = st
    messages = types.ModuleType("langchain_core.messages")
    messages.HumanMessage, messages.AIMessage = HumanMessage, AIMessage
    sys.modules["langchain_core"] = types.ModuleType("langchain_core")
    sys.modules["langchain_core.messages"] = messages
    mongo = types.ModuleType("pymongo")
    mongo.MongoClient = lambda *a, **k: CURRENT.get().sql_client
    sys.modules["pymongo"] = mongo
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: False
    sys.modules["dotenv"] = dotenv
    # The original saver indexes this variable. The driver above is local only.
    os.environ["PYMONGO_CONNECTION_STRING"] = "local-adapter://sqlite"
    sys.path.insert(0, str(ROOT / "vendor/FYP-project"))


@contextlib.contextmanager
def bind(state):
    token = CURRENT.set(state)
    try: yield
    finally: CURRENT.reset(token)
