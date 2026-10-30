"""Optional immutable local Qdrant generation; no network client or auto-download."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from uuid import NAMESPACE_URL, uuid5

from tcm.local_dense import DenseRetriever
from tcm.mineru_pharmacopoeia import digest


def point_id(chunk_id):
    return str(uuid5(NAMESPACE_URL, 'tcm-pharmacopoeia:' + chunk_id))


def check_receipt(receipt, dense_receipt):
    for key in ('index_sha256', 'model_fingerprint', 'vectors_sha256', 'items_sha256', 'dimension', 'count'):
        if receipt[key] != dense_receipt[key]:
            raise ValueError('Qdrant source/model generation mismatch')
    if receipt['format_version'] != 1 or receipt['collection'] != 'pharmacopoeia':
        raise ValueError('Unknown Qdrant generation')


@contextmanager
def locked_generation(directory, timeout=10):
    """SDK local mode owns a collection exclusively; bound wait outside its lock."""
    directory = Path(directory)
    if not directory.is_dir() or not (directory / 'receipt.json').is_file():
        raise ValueError('Completed Qdrant generation required')
    with (directory / '.query.lock').open('a') as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Qdrant generation busy')
                time.sleep(.05)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def build_generation(dense, output):
    """New directory only; publish receipt after all points and integrity checks."""
    from qdrant_client import QdrantClient, models
    output = Path(output)
    if output.exists():
        raise FileExistsError('Use a fresh generation path')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.qdrant-build-', dir=output.parent))
    client = None
    try:
        client = QdrantClient(path=str(stage / 'store'))
        client.create_collection('pharmacopoeia', vectors_config=models.VectorParams(
            size=dense.encoder.dimension, distance=models.Distance.COSINE))
        for offset in range(0, len(dense.items), 128):
            points=[]
            for i in range(offset, min(offset + 128, len(dense.items))):
                item = dense.items[i]
                points.append(models.PointStruct(id=point_id(item['chunk_id']), vector=dense.vectors[i].tolist(),
                    payload={**item, 'index_sha256':dense.receipt['index_sha256'],
                             'research_scope':not item['scope'].startswith('appendix:')}))
            client.upsert('pharmacopoeia', points=points, wait=True)
        count = client.count('pharmacopoeia', exact=True).count
        if count != dense.receipt['count']:
            raise ValueError('Qdrant point count mismatch')
        client.close();client=None
        hashes = {str(p.relative_to(stage)): digest(p) for p in (stage/'store').rglob('*')
                  if p.is_file() and p.name != '.lock'}
        from importlib.metadata import version
        receipt = {**dense.receipt, 'format_version':1, 'collection':'pharmacopoeia',
                   'qdrant_client_version':version('qdrant-client'), 'files':hashes}
        (stage/'receipt.json').write_text(json.dumps(receipt, indent=2))
        # Refuse replacement: immutable build paths are the deployment/rollback unit.
        if output.exists():raise FileExistsError('Generation appeared during build')
        stage.rename(output)
        return receipt
    finally:
        if client is not None:client.close()
        if stage.exists():shutil.rmtree(stage)


class QdrantDenseRetriever(DenseRetriever):
    def __init__(self, retriever, encoder, cache, generation):
        super().__init__(retriever, encoder, cache)
        self.generation = Path(generation)
        self.qreceipt = json.loads((self.generation/'receipt.json').read_text())
        check_receipt(self.qreceipt, self.receipt)

    def rank(self, vector, k, allowed):
        from qdrant_client import QdrantClient, models
        # This adapter deliberately disallows unscoped dense retrieval.
        if allowed is None:
            raise ValueError('Qdrant research requires entity filtering')
        if not allowed:return []
        items=[self.items[i] for i in allowed]
        names=sorted({x['name'] for x in items})
        if len(names)!=1:raise ValueError('Exactly one resolved herb required')
        filters=models.Filter(must=[
            models.FieldCondition(key='name',match=models.MatchValue(value=names[0])),
            models.FieldCondition(key='index_sha256',match=models.MatchValue(value=self.receipt['index_sha256'])),
            models.FieldCondition(key='research_scope',match=models.MatchValue(value=True)),
            models.FieldCondition(key='chunk_id',match=models.MatchAny(any=[x['chunk_id'] for x in items]))])
        with locked_generation(self.generation):
            for name, expected in self.qreceipt['files'].items():
                path=(self.generation/name).resolve()
                if not path.is_relative_to(self.generation.resolve()) or digest(path)!=expected:
                    raise ValueError('Qdrant generation modified')
            client=QdrantClient(path=str(self.generation/'store'))
            try:
                if client.count('pharmacopoeia',exact=True).count!=self.receipt['count']:
                    raise ValueError('Qdrant point count changed')
                # Fetch scoped candidates before stable ID tie breaking, as in numpy baseline.
                found=client.query_points('pharmacopoeia',query=vector.tolist(),query_filter=filters,
                    limit=len(allowed),with_payload=True,with_vectors=False).points
            finally:client.close()
        by_id={self.identifiers[i]:i for i in allowed}
        result=[]
        for point in found:
            chunk=point.payload['chunk_id']
            if chunk not in by_id or str(point.id)!=point_id(chunk):
                raise ValueError('Qdrant returned an unbound source')
            result.append((by_id[chunk],float(point.score)))
        return sorted(result,key=lambda x:(-x[1],self.identifiers[x[0]]))[:k]
