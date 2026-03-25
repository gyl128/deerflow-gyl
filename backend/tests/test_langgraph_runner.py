import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'run-langgraph.sh'


def test_prod_runner_falls_back_without_license():
    result = subprocess.run(
        ['bash', str(SCRIPT_PATH), 'prod', '--help'],
        capture_output=True,
        text=True,
        check=False,
        env={},
    )

    assert result.returncode == 0
    assert 'LANGGRAPH_RUNTIME_FALLBACK' in result.stderr
    assert '--no-reload' in result.stdout
