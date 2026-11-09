import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .review_reliability import parse_json_response, json_failure_diagnostic, verification_pending
from .diff_evidence import locate_diff
from .services import _apply_verification_results, _verify_findings_against_target, _repository_guard
from .review_mcp import CodeReviewMCP

class JsonResponseTests(SimpleTestCase):
    def test_embedded_fence_is_data(self):
        value={'items':[{'reason':'code ```swift\nprint("x")\n```'}]}
        self.assertEqual(parse_json_response(json.dumps(value)),value)
        self.assertEqual(parse_json_response('```json\n'+json.dumps(value)+'\n```'),value)

    def test_text_blocks_and_truncation(self):
        self.assertEqual(parse_json_response([{'type':'text','text':'{"items":[]}' }]),{'items':[]})
        with self.assertRaises(json.JSONDecodeError):parse_json_response('{"items":[{"reason":"truncated')

    def test_diagnostic_contains_no_source_or_secret(self):
        response=SimpleNamespace(content='{"secret":"my-private-key" "x":1}',response_metadata={'finish_reason':'length'},usage_metadata={'output_tokens':20})
        try:parse_json_response(response.content)
        except json.JSONDecodeError as exc:d=json_failure_diagnostic(response,exc)
        self.assertEqual(d['finish_reason'],'length')
        self.assertNotIn('my-private-key',json.dumps(d))
        self.assertEqual(d['position'],27)

class DiffLocationTests(SimpleTestCase):
    def test_empty_lines_and_file_comment_do_not_match(self):
        lines=['// Header.swift','', 'import UIKit','']+['let unrelated%d = 0'%i for i in range(30)]+['func handleKnow() {','    exit(0)','}']
        diff='@@ -0,0 +1,%d @@\n'%len(lines)+'\n'.join('+'+x for x in lines)
        result=locate_diff({'line_start':1,'evidence':'handleKnow 方法直接调用 `exit(0)`'},diff,2)
        self.assertEqual(result['line_start'],36)
        self.assertIn('+    exit(0)',result['related_diff'])
        self.assertNotIn('Header.swift',result['related_diff'])
        self.assertTrue(result['related_diff'].startswith('@@ -0,0 +34,4 @@'))

    def test_removed_line_side_and_no_match(self):
        diff='@@ -90,3 +90,2 @@\n before\n-oldUnsafe()\n after'
        found=locate_diff({'line_start':1,'evidence':'`oldUnsafe()`'},diff,1)
        self.assertEqual(found['line_side'],'old')
        self.assertEqual(found['line_start'],91)
        unresolved=locate_diff({'evidence':'notPresentIdentifier'},diff)
        self.assertEqual(unresolved['diff_location_status'],'unresolved')
        self.assertEqual(unresolved['related_diff'],diff)

class VerificationRecoveryTests(SimpleTestCase):
    def test_failure_preserves_high_risk_and_pending_status(self):
        result,_=_apply_verification_results([{'key':'k','severity':'high','disposition':'confirmed'}],[])
        self.assertEqual(result[0]['disposition'],'needs_confirmation')
        self.assertEqual(result[0]['severity'],'high')
        self.assertTrue(verification_pending(result))

    @patch('code_analysis.services._parse_stage_json')
    @patch('code_analysis.services._related_document_context',return_value=[])
    @patch('code_analysis.review_mcp.CodeReviewMCP')
    @patch('langgraph_integration.views.create_llm_instance')
    @patch('code_analysis.services._get_code_analysis_llm_config',return_value={})
    def test_batch_failure_recovers_as_single_candidates(self,config,create,mcp,docs,parse):
        mcp.return_value.read_file.return_value='func auth() { return false }'
        mcp.return_value.search_code.return_value=[]
        create.return_value.invoke.return_value=SimpleNamespace(content='',usage_metadata={})
        def decode(response,task_id,stage,keys):
            if len(keys)>1:raise json.JSONDecodeError('bad','{}',1)
            return {'items':[{'key':keys[0],'verdict':'needs_confirmation','severity':'medium','reason':'需要业务确认','supporting_evidence':[],'trigger_reachable':False,'counter_evidence':'','missing_evidence':'业务约束'}]}
        parse.side_effect=decode
        task=SimpleNamespace(pk='task',mode='standard',head_sha='a'*40)
        findings=[{'key':str(i),'file':'a.swift','severity':'medium','disposition':'needs_confirmation','evidence':'auth()'} for i in range(4)]
        active,rejected,_,note=_verify_findings_against_target(task,findings)
        self.assertEqual(len(active),4)
        self.assertFalse(rejected)
        self.assertTrue(all(x['target_commit_verified'] for x in active))
        self.assertIn('4/4',note)
        self.assertEqual(parse.call_count,5)

class RepositoryAndScannerTests(SimpleTestCase):
    def test_workspace_lock_serializes_threads(self):
        with tempfile.TemporaryDirectory() as directory, patch('code_analysis.services.OCR_WORKSPACE_ROOT',Path(directory)):
            entered=threading.Event();release=threading.Event();second=threading.Event()
            def first():
                with _repository_guard(SimpleNamespace(pk='same')):
                    entered.set();release.wait(3)
            def other():
                with _repository_guard(SimpleNamespace(pk='same')):second.set()
            a=threading.Thread(target=first);b=threading.Thread(target=other)
            a.start();self.assertTrue(entered.wait(1));b.start()
            try:self.assertFalse(second.wait(.1))
            finally:release.set();a.join(3);b.join(3)
            self.assertTrue(second.is_set())

    def test_failed_scanner_never_reports_full_coverage(self):
        mcp=CodeReviewMCP(SimpleNamespace())
        mcp.static_coverage={'requested_files':2}
        mcp._normalize_semgrep({'results':[],'errors':[{'message':'parse failed'}],'paths':{'scanned':['a.py','b.py']},'scanner_exit_code':2})
        self.assertEqual(mcp.static_coverage['percent'],0)
        mcp._normalize_semgrep({'results':[],'errors':[],'paths':{'scanned':['a.py']},'scanner_exit_code':0})
        self.assertEqual(mcp.static_coverage['percent'],50)
