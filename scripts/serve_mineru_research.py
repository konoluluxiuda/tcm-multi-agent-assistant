"""Serve the latest unreviewed MinerU research index on loopback only."""
import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path

from tcm.mineru_research import ResearchService, make_handler
from tcm.mineru_chat import ResearchChat

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    parser.add_argument('--high', type=Path, default=ROOT / 'data/《中国药典》2025年版 一部.pdf')
    parser.add_argument('--full', type=Path, default=ROOT / 'data/《中国药典》2025年版 一部 全本.pdf')
    parser.add_argument('--port', type=int, default=7869)
    parser.add_argument('--memory', type=Path, default=ROOT / 'var/mineru_research_chat/conversations.sqlite3')
    args = parser.parse_args()
    service = ResearchService(args.directory, {'high': args.high, 'full': args.full})
    chat = ResearchChat(service, args.memory)
    with ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(service, chat)) as server:
        print(f'MinerU research: http://127.0.0.1:{server.server_port}/', flush=True)
        print(f'Research conversation: http://127.0.0.1:{server.server_port}/chat', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
