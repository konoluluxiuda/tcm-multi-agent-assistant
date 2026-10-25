"""Offline estimates are kept separate from provider-reported token usage."""
import json
from pydantic import Field
from tcm.task_tools import Contract


class InputBudget(Contract):
    per_call: int = Field(default=56000,ge=1,le=1000000)
    cumulative: int = Field(default=160000,ge=1,le=8000000)


class TokenEstimator:
    def __init__(self, encoding=None, *, label=None):
        # Caller may inject an already-loaded local tokenizer. No loading or
        # downloading here; even a BPE count isn't provider chat-template usage.
        self.encoding=encoding
        self.label=label or ('local_bpe_estimate' if encoding is not None else 'utf8_byte_proxy')

    def count(self,messages,tools):
        raw=json.dumps({'messages':[m.model_dump(mode='json') for m in messages],
                        'tools':tools},ensure_ascii=False,separators=(',',':'))
        if self.encoding is None:return len(raw.encode('utf-8'))
        return len(self.encoding.encode(raw,disallowed_special=()))
