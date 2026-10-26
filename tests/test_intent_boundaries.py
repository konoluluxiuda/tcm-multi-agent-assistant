import unittest
from unittest.mock import patch
from tcm.intent_planner import QueryIntent, mapped, grounded
from tcm.assistant_router import resolve


class BoundaryTests(unittest.TestCase):
    def test_stopped_entities_cannot_become_queries(self):
        for action in ('clarify','unsupported'):
            proposal={'action':action,'entities':['示例叶']}
            intent=QueryIntent.model_validate(proposal)
            self.assertEqual(intent.entities,[]);self.assertEqual(proposal['entities'],['示例叶'])
            self.assertEqual(mapped(intent,'示例叶资料')['tool'],'assistant')
            for extra in ({'fields':['注意']},{'relation_kind':'target'},
                          {'priority_field':'注意','priority_evidence':'注意'}):
                with self.assertRaises(ValueError):QueryIntent.model_validate({**proposal,**extra})

    def test_partial_chinese_and_ascii_names_stop(self):
        for entity,question in [('试验叶','虚构试验叶能派什么用场'),
            ('黄芪','蒙古黄芪的功效'),('白术','炒白术的功效'),
            ('白术','白术胶囊的功效'),('甲草','复方甲草的用途'),
            ('Alpha','AlphaBeta的资料'),('Beta','AlphaBeta的资料')]:
            self.assertFalse(grounded(entity,question),(entity,question))
        for entity,question in [('虚构试验叶','请把虚构试验叶能派什么用场找来'),
            ('蒙古黄芪','整理蒙古黄芪的资料'),('白术胶囊','只看白术胶囊的资料'),
            ('AlphaBeta','AlphaBeta的资料')]:
            self.assertTrue(grounded(entity,question),(entity,question))

    def test_mixed_unsupported_is_shared_and_does_not_use_model(self):
        with patch.dict('os.environ',{'ENABLE_NATURAL_ROUTING':'1'}):
            for q in ('黄芪的功效，再帮我采购','当归的注意事项和售价','白术的成分和购买方式'):
                result=resolve(q)
                self.assertEqual(result['tool'],'assistant')
                self.assertTrue(result['request']['unsupported'])
                with self.assertRaises(ValueError):mapped(QueryIntent(action='research',entities=[q[:2]],fields=['功能与主治']),q)
            self.assertEqual(resolve('当归买回来放在哪里保存？')['tool'],'research')
            self.assertEqual(resolve('/research 当归的功效')['tool'],'research')


if __name__=='__main__':unittest.main()
