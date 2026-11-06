"""Load direct-network recovery independently from Work/side-intake settings."""
from pathlib import Path

MOD_DIR = Path(__file__).parent / 'claude_mods' / 'ciel-network-retry'


def launch_args(provider, environment, *, native=False):
    if (native or provider != 'cielairouter'
            or environment.get('CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE') != '1'
            or environment.get('CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES') in (None, '', '0')):
        return []
    return ['--plugin-dir', str(MOD_DIR)]
