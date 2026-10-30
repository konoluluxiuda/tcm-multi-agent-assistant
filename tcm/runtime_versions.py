"""Version binding for persisted execution; data snapshots remain separately bound."""
from importlib.metadata import PackageNotFoundError, version


def framework_versions():
    result = {}
    for name in ('langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-postgres',
                 'langchain-core', 'langchain-openai', 'openai'):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result
