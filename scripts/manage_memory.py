"""Purge expired conversation payloads; never prints messages or session tokens."""
from dotenv import load_dotenv
from agents.conversation_store import ConversationStore, ROOT

if __name__ == '__main__':
    load_dotenv(ROOT / '.env')
    print({'expired_conversations_purged': ConversationStore.from_env().purge_expired()})
