import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts.build_mineru_retrieval import build
from tests.test_mineru_retrieval import fixture
from tests.test_mineru_chat import Service
from tcm.assistant_router import resolve
from tcm.mineru_chat import ResearchTool
from tcm.report_request import bounded_field_request


class ChatFieldRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);fixture(root/'source');build(root/'source',root/'index')
        self.service=Service(root/'index');self.tool=ResearchTool(self.service)

    def test_router_keeps_original_and_tool_replays_source(self):
        q='整理示例叶的资料，想知道它主要用来做什么。'
        with patch.dict('os.environ',{'ENABLE_NATURAL_ROUTING':'1'}):
            routed=resolve(q)
        self.assertEqual(routed['tool'],'research');self.assertEqual(routed['request']['question'],q)
        result,context=self.tool.execute(q,None)
        self.assertEqual(result['status'],'candidates');self.assertEqual(result['llm_calls'],0)
        self.assertEqual(context['last_question'],q)
        replay,_=self.tool.execute('出处呢',context)
        self.assertEqual(replay['tool_result']['hits'],result['tool_result']['hits'])
        self.assertEqual(self.service.queries,['示例叶的功能与主治']*2)

    def test_entity_is_not_narrowed_and_unknown_stays_unknown(self):
        for name in ('酒示例叶','示例叶胶囊','不存在药材','示例叶与其他药'):
            q=name+'怎么保存？'
            request=bounded_field_request(q)
            self.assertEqual(request['herb'],name)
            result,_=self.tool.execute(q,None)
            self.assertNotEqual(result['status'],'candidates')

    def test_extra_clause_not_erased(self):
        self.assertIsNone(bounded_field_request('示例叶怎么保存？还要比较另一味药'))
        self.assertIsNone(bounded_field_request('整理示例叶的资料，想知道它主要用来做什么。再推荐用量'))

    def test_bookmark_binding_roundtrip_and_invalid_digest(self):
        from tcm.agent_research import pack_contexts
        from tcm.research_bookmarks import capture
        from tcm.bookmark_backup import validate_item, InvalidBookmarkBackup
        _,context=self.tool.execute('示例叶的功效',None)
        payload=capture(pack_contexts(None,context))
        self.assertTrue(self.tool.valid_context(payload['focus']))
        item={'title':'test','note':'','payload':payload}
        validate_item(item)
        payload['focus']['request_sha256']='invalid'
        with self.assertRaises(InvalidBookmarkBackup):validate_item(item)

    def test_request_version_invalidates_old_context(self):
        _,context=self.tool.execute('示例叶的功效',None)
        self.assertTrue(self.tool.valid_context(context))
        context.pop('request_sha256')
        self.assertEqual(self.tool.execute('出处呢',context)[0]['status'],'context_stale')


if __name__=='__main__':unittest.main()
