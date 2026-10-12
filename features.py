"""Startup feature switches, without model or optional SDK imports."""
import os
from dataclasses import dataclass


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {'1', 'true', 'yes', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'{name} must be a boolean (true/false or 1/0)')


@dataclass(frozen=True)
class FeatureFlags:
    image_analysis: bool = False
    speech: bool = False
    web_search: bool = False
    speech_credentials: bool = False
    web_credentials: bool = False

    @classmethod
    def from_env(cls):
        return cls(env_bool('ENABLE_IMAGE_ANALYSIS'), env_bool('ENABLE_SPEECH'),
                   env_bool('ENABLE_WEB_SEARCH'), bool(os.getenv('ELEVEN_LABS_API_KEY', '').strip()),
                   bool(os.getenv('TAVILY_API_KEY', '').strip()))

    def state(self, name):
        enabled = getattr(self, name)
        credentials = self.speech_credentials if name == 'speech' else self.web_credentials if name == 'web_search' else True
        return {'enabled': enabled, 'available': enabled and credentials,
                'reason': 'disabled' if not enabled else 'missing_credentials' if not credentials else None}

    def as_dict(self):
        return {name: self.state(name) for name in ('image_analysis', 'speech', 'web_search')}
