"""Real application UI with a large fabricated catalog and all providers disabled."""
import atexit
import os
from scripts.run_offline_ci import test_environment
os.environ.update(test_environment())
from tests.research_offload_fixture import LargeBusinessFixture
fixture=LargeBusinessFixture();fixture.__enter__()
atexit.register(fixture.__exit__,None,None,None)
os.environ.update(ENABLE_RESEARCH_TASKS='1',ENABLE_NATURAL_ROUTING='1',RESEARCH_TOOLS_ONLY='1',
    ENABLE_INTENT_PLANNER='0',ENABLE_EVIDENCE_ASSISTANT='0',ENABLE_EVIDENCE_AUTO_ROUTING='0',
    ENABLE_PHARMACOPOEIA_RESEARCH='0',ENABLE_PUBMED='0',ENABLE_RESEARCH_BOOKMARKS='0',
    CHAT_MEMORY_DB=str(fixture.root/'chat.sqlite3'),RESEARCH_RUN_DB=str(fixture.root/'runs.sqlite3'),
    RESEARCH_TASK_DB=str(fixture.root/'tasks.sqlite3'),RESEARCH_JOB_DSN='',RESEARCH_CHECKPOINT_DSN='',
    CHAT_COOKIE_NAME='synthetic_offload_browser')
import app as application
app=application.app
