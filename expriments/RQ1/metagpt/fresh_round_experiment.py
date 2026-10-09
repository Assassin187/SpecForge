#!/usr/bin/env python3
"""Third independent four-protocol MetaGPT round: generation followed by native C99 QA."""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CASES = ('mqtt', 'coap', 'http11', 'smtp')
PYTHON = BASE / '.venv/bin/python'


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def generation_worker(case, batch):
    manifest = json.loads((batch / 'batch.json').read_text())
    run = Path(manifest['cases'][case]['generation_run'])
    assert not run.exists(), 'Each independent generation starts from an empty directory.'
    # Native imports create tool schemas and logs before the recording entrypoint
    # creates its fresh output directory. Keep those runtime files separate.
    os.environ['METAGPT_PROJECT_ROOT'] = str(batch / 'runtime' / case)
    # Register the shared provider before any role can cache its default LLM.
    spec = importlib.util.spec_from_file_location('third_round_shared_model', BASE / 'run.py')
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    from openai._base_client import AsyncHttpxClientWrapper
    original_init = AsyncHttpxClientWrapper.__init__

    class WireConfigurationMismatch(BaseException):
        pass

    async def validate_request(request):
        body = json.loads(await request.aread())
        expected = manifest['model']
        if not (body['model'] == expected['model'] and body['max_tokens'] == expected['max_tokens']
                and not body.get('stream', False) and body['thinking'] == {'type': 'enabled'}
                and body['reasoning_effort'] == expected['reasoning_effort']):
            raise WireConfigurationMismatch('Shared model settings mismatch; blocked before network.')

    def validated_init(self, *args, **kwargs):
        hooks = dict(kwargs.get('event_hooks', {}))
        hooks['request'] = [validate_request, *hooks.get('request', [])]
        kwargs['event_hooks'] = hooks
        return original_init(self, *args, **kwargs)

    AsyncHttpxClientWrapper.__init__ = validated_init
    import metagpt.team as native_team
    native_team.SERDESER_PATH = run / 'workspace/storage'
    spec = importlib.util.spec_from_file_location('third_round_generation', BASE / 'mqtt_experiment.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    sys.argv = [str(BASE / 'mqtt_experiment.py'), '--case', case, '--out', str(run)]
    code = entry.main()
    state = json.loads((run / 'run.json').read_text())
    assert state['fresh'] and not state['resumed'] and state['model'] == manifest['model']
    shutil.copyfile(__file__, run / 'reports/fresh_round_entrypoint.py')
    log = run / 'logs/framework.log'
    text = log.read_text(errors='replace') if log.exists() else ''
    caught = bool(re.search(r'\| ERROR\s+\| metagpt\.utils\.common:wrapper:.*Exception occurs, start to serialize', text))
    budget = bool(re.search(r'^metagpt\.utils\.common\.NoMoneyException:', text, re.M))
    state['native_sop_finished_without_framework_error'] = state['status'] == 'generated' and not caught
    state['native_generation_stop'] = {
        'reason': 'native_budget_exhausted' if budget else 'native_caught_framework_exception' if caught
        else state['stop_reason'],
        'generate_repo_returned_normally': state['native_pipeline_completed'],
        'sop_without_caught_framework_error': state['native_sop_finished_without_framework_error'],
    }
    state['batch'] = {'round': 3, 'manifest': str(batch / 'batch.json'),
                      'full_workflow_includes_native_feedback': True,
                      'shared_model_setup_loaded_before_native_roles': True,
                      'wire_configuration_checked_before_every_network_call': True,
                      'response_replay': False, 'previous_project_reuse': False,
                      'native_checkpoint_storage_isolated': True}
    checkpoint = native_team.SERDESER_PATH / 'team/team.json'
    if checkpoint.exists():
        frozen = run / 'reports/native_checkpoint/team.json'
        frozen.parent.mkdir()
        assert os.environ['DS_API'].encode() not in checkpoint.read_bytes()
        shutil.copyfile(checkpoint, frozen)
        state['native_generation_stop']['checkpoint'] = str(frozen.relative_to(run))
    save(run / 'run.json', state)
    return code


def prepare(batch):
    import feedback_experiment as feedback
    sys.path.insert(0, str(BASE.parents[2]))
    from specforge.llm import ModelConfig
    batch.mkdir(parents=True, exist_ok=False)
    (batch / 'logs').mkdir()
    (batch / 'reports').mkdir()
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    state = {'round': 3, 'created_at': now(), 'status': 'preparing',
             'fresh_independent_runs': True, 'full_native_feedback_enabled': True,
             'model': ModelConfig().record(), 'limits': feedback.limits(),
             'prior_feedback_batch': str(BASE / 'runs/feedback_20261009T002030Z'),
             'generation_entrypoint_unchanged': True,
             'phase_order': ['fresh_generation', 'freeze_originals', 'native_qa_feedback', 'freeze_final_outputs',
                             'independent_assessment_without_feedback'],
             'cases': {case: {'generation_run': str(BASE / 'runs' / f'{case}_{identifier}'),
                              'process_log': f'logs/{case}.generation.log'} for case in CASES}}
    save(batch / 'batch.json', state)
    for name in ('fresh_round_experiment.py', 'feedback_experiment.py', 'mqtt_experiment.py', 'run.py'):
        shutil.copyfile(BASE / name, batch / 'reports' / name)
    with (batch / 'reports/c99-adaptation.patch').open('w') as patch:
        subprocess.run(['git', '-C', str(BASE / 'MetaGPT'), 'diff', '--', 'metagpt'], stdout=patch, check=True)

    def preflight(case):
        check = batch / 'reports/input_preflight' / case
        result = subprocess.run([str(PYTHON), str(BASE / 'mqtt_experiment.py'), '--case', case,
                                 '--out', str(check), '--prepare-only'], text=True, capture_output=True)
        (batch / 'logs' / f'{case}.preflight.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, f'{case} input preflight failed; see its log.'
        record = json.loads((check / 'run.json').read_text())
        previous = Path(feedback.SOURCES[CASES.index(case)])
        original = json.loads((BASE / 'runs' / previous / 'run.json').read_text())
        assert record['idea']['sha256'] == original['idea']['sha256']
        assert record['model'] == original['model'] == state['model']
        assert record['usage']['http_attempts'] == 0
        assert not Path(state['cases'][case]['generation_run']).exists()
        return case, {'original_idea_sha256': record['idea']['sha256'], 'input_hashes': record['input_hashes'],
                      'input_equivalent_to_previous_round': True, 'preflight_llm_requests': 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for case, evidence in pool.map(preflight, CASES):
            state['cases'][case].update(evidence)
    state.update(status='prepared', zero_call_preflight_passed=True, prepared_at=now())
    save(batch / 'batch.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--generation-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--case', choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    batch = args.out.resolve()
    if args.generation_worker:
        return generation_worker(args.case, batch)
    state = json.loads((batch / 'batch.json').read_text()) if batch.exists() else prepare(batch)
    assert state['status'] == 'prepared', 'Only a prepared fresh batch can start; existing runs are never regenerated.'
    if args.prepare_only:
        print(f'Four fresh inputs verified, zero LLM calls: {batch}', flush=True)
        return 0
    started = time.monotonic()
    state.update(status='generating', started_at=now())
    save(batch / 'batch.json', state)

    def launch(case):
        command = [str(PYTHON), str(BASE / 'fresh_round_experiment.py'), '--generation-worker',
                   '--case', case, '--out', str(batch)]
        with (batch / state['cases'][case]['process_log']).open('w') as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=BASE).returncode
        return case, code

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for case, code in pool.map(launch, CASES):
            original = json.loads((Path(state['cases'][case]['generation_run']) / 'run.json').read_text())
            state['cases'][case].update(generation_process_exit_code=code, generation_status=original['status'],
                                       generation_usage=original['usage'], source_hashes=original.get('project_hashes'))
            save(batch / 'batch.json', state)
            print(f'{case}: independent generation ended, exit {code}', flush=True)
    state['generation_wall_seconds'] = round(time.monotonic() - started, 3)
    assert all(item['generation_status'] == 'generated' for item in state['cases'].values()), 'See retained generation errors.'
    state.update(status='native_feedback', generation_finished_at=now())
    save(batch / 'batch.json', state)
    feedback_output = batch / 'feedback'
    command = [str(PYTHON), str(BASE / 'feedback_experiment.py'), '--out', str(feedback_output), '--jobs', '4',
               '--round', '3', '--sources', *[Path(state['cases'][case]['generation_run']).name for case in CASES]]
    with (batch / 'logs/native_feedback.log').open('w') as log:
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=BASE).returncode
    state.update(status='feedback_completed' if code == 0 else 'feedback_failed', feedback_process_exit_code=code,
                 feedback_output=str(feedback_output), feedback_finished_at=now(),
                 generation_and_feedback_wall_seconds=round(time.monotonic() - started, 3))
    save(batch / 'batch.json', state)
    print(f'Third-round generation and native feedback ended: {batch}', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
