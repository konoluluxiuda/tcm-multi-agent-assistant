"""Saved model/source text cannot break out of the offline replay JSON script."""
from html.parser import HTMLParser
import json
import unittest
from scripts.replay_evidence_demo import render_page


class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.tags=[];self.payload=''
    def handle_starttag(self,tag,attrs):
        if tag=='script':
            self.tags.append(dict(attrs));self.current=dict(attrs).get('id')
    def handle_endtag(self,tag):
        if tag=='script':self.current=None
    def handle_data(self,data):
        if self.current=='replay-data':self.payload+=data


class ReplayTests(unittest.TestCase):
    def test_untrusted_source_and_model_strings_remain_json_data(self):
        for text in ('</script><script>window.injected=true</script>',
                     '<img src=x onerror=alert(1)>','中文 & < > \u2028\u2029'):
            data={'message':text}
            parser=Scripts();parser.feed(render_page(data))
            self.assertEqual(len(parser.tags),2)
            self.assertEqual(json.loads(parser.payload),data)
