#!/usr/bin/env python3
"""Independent four-protocol MetaGPT round: generation followed by native C99 QA."""
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


def generation_worker(case, batch, resume=False):
    manifest = json.loads((batch / 'batch.json').read_text())
    os.environ['METAGPT_MODEL_CONFIG'] = json.dumps(manifest['model'])
    run = Path(manifest['cases'][case]['generation_run'])
    if not resume:
        assert not run.exists(), 'Each independent generation starts from an empty directory.'
    else:
        assert (run / 'workspace/storage/team/team.json').is_file()
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
                and not body.get('stream', False)
                and body.get('thinking') == setup.MODEL.extra_body.get('thinking')
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
    if resume:
        sys.argv.append('--resume')
    prior_jobs = len(json.loads((run / 'run.json').read_text())['jobs']) if resume else 0
    code = entry.main()
    state = json.loads((run / 'run.json').read_text())
    assert state['fresh'] and state['resumed'] == resume and state['model'] == manifest['model']
    shutil.copyfile(__file__, run / 'reports/fresh_round_entrypoint.py')
    log = run / 'logs/framework.log'
    text = log.read_text(errors='replace') if log.exists() else ''
    caught = (any(job['status'] == 'failed' for job in state['jobs'][prior_jobs:]) or not state['native_pipeline_completed']
              if resume else bool(re.search(r'\| ERROR\s+\| metagpt\.utils\.common:wrapper:.*Exception occurs, start to serialize', text)))
    budget = bool(re.search(r'^metagpt\.utils\.common\.NoMoneyException:', text, re.M))
    state['native_sop_finished_without_framework_error'] = state['status'] == 'generated' and not caught
    state['native_generation_stop'] = {
        'reason': 'native_budget_exhausted' if budget else 'native_caught_framework_exception' if caught
        else state['stop_reason'],
        'generate_repo_returned_normally': state['native_pipeline_completed'],
        'sop_without_caught_framework_error': state['native_sop_finished_without_framework_error'],
    }
    state['batch'] = {'round': manifest['round'], 'manifest': str(batch / 'batch.json'),
                      'full_workflow_includes_native_feedback': True,
                      'shared_model_setup_loaded_before_native_roles': True,
                      'wire_configuration_checked_before_every_network_call': True,
                      'response_replay': False, 'previous_project_reuse': resume,
                      'native_checkpoint_resume': resume,
                      'native_checkpoint_storage_isolated': True}
    checkpoint = native_team.SERDESER_PATH / 'team/team.json'
    if checkpoint.exists():
        frozen = run / 'reports/native_checkpoint/team.json'
        frozen.parent.mkdir(exist_ok=True)
        assert os.environ[manifest['model']['key_env']].encode() not in checkpoint.read_bytes()
        shutil.copyfile(checkpoint, frozen)
        state['native_generation_stop']['checkpoint'] = str(frozen.relative_to(run))
    save(run / 'run.json', state)
    return code


def prepare(batch, round_number=3, assessment_script=None, generation_root=None):
    import feedback_experiment as feedback
    sys.path.insert(0, str(BASE.parents[2]))
    from specforge.llm import ModelConfig
    model = (ModelConfig.from_record(json.loads(os.environ['METAGPT_MODEL_CONFIG']))
             if 'METAGPT_MODEL_CONFIG' in os.environ else ModelConfig())
    batch.mkdir(parents=True, exist_ok=False)
    (batch / 'logs').mkdir()
    (batch / 'reports').mkdir()
    generation_root = (generation_root or BASE / 'runs').resolve()
    generation_root.mkdir(parents=True, exist_ok=True)
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    state = {'round': round_number, 'created_at': now(), 'status': 'preparing',
             'fresh_independent_runs': True, 'full_native_feedback_enabled': True,
             'model': model.record(), 'limits': feedback.limits(),
             'prior_feedback_batch': str(BASE / 'runs/feedback_20261009T002030Z'),
             'generation_entrypoint_unchanged': True,
             'phase_order': ['fresh_generation', 'freeze_originals', 'native_qa_feedback', 'freeze_final_outputs']
                            + (['independent_assessment_without_feedback'] if assessment_script else []),
             'cases': {case: {'generation_run': str(generation_root / f'{case}_{identifier}'),
                              'process_log': f'logs/{case}.generation.log'} for case in CASES}}
    if assessment_script:
        shutil.copyfile(assessment_script, batch / 'reports/post_feedback_assessment.py')
        state['independent_assessment_entrypoint'] = 'reports/post_feedback_assessment.py'
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
        assert record['model'] == state['model']
        assert record['usage']['http_attempts'] == 0
        assert not Path(state['cases'][case]['generation_run']).exists()
        return case, {'original_idea_sha256': record['idea']['sha256'], 'input_hashes': record['input_hashes'],
                      'input_equivalent_to_previous_round': True,
                      'model_config_equal_to_previous_round': record['model'] == original['model'],
                      'preflight_llm_requests': 0}

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
    parser.add_argument('--resume', action='store_true', help='Continue this paused batch from native checkpoints and preserved in-memory workers.')
    parser.add_argument('--serial', action='store_true', help='Finish all initial generation sequentially, then repair and assess one protocol at a time.')
    parser.add_argument('--round', type=int, choices=(1, 2, 3, 4, 5), default=3, dest='round_number')
    parser.add_argument('--generation-root', type=Path, help='Directory for the fresh per-protocol generation records.')
    parser.add_argument('--assessment-script', type=Path, help='Run the existing assessor only after all feedback workers stop.')
    parser.add_argument('--generation-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--case', choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    batch = args.out.resolve()
    if args.generation_worker:
        return generation_worker(args.case, batch, resume=args.resume)
    state = (json.loads((batch / 'batch.json').read_text()) if batch.exists()
             else prepare(batch, args.round_number, args.assessment_script, args.generation_root))
    cases = tuple(state['cases'])
    os.environ['METAGPT_MODEL_CONFIG'] = json.dumps(state['model'])
    required_status = ('paused_for_serial_switch' if args.serial else 'paused_for_gateway_diagnosis') if args.resume else 'prepared'
    assert state['status'] == required_status, 'Batch status does not permit this launch.'
    if args.resume and not args.serial:
        assert json.loads(Path(state['gateway_parallel_validation']).read_text())['passed']
    if args.prepare_only:
        print(f'Four fresh inputs verified, zero LLM calls: {batch}', flush=True)
        return 0
    started = time.monotonic()
    state.update(status='generating', execution_mode='serial' if args.serial else 'parallel',
                 **{'continuation_started_at' if args.resume else 'started_at': now()})
    if args.serial:
        state['serial_schedule'] = {'initial_generation_order': list(cases), 'feedback_and_assessment_order': list(cases),
                                    'all_initial_generation_before_feedback': True,
                                    'native_feedback_then_independent_assessment_per_protocol': True,
                                    'concurrent_protocols': 1}
    if args.resume:
        state.setdefault('generation_continuations', []).append({'started_at': now(),
                     'native_checkpoint_resume': True, 'new_generation': False,
                     'parallel_protocols': 1 if args.serial else 4, 'execution_mode': state['execution_mode']})
    save(batch / 'batch.json', state)

    def launch(case):
        run = Path(state['cases'][case]['generation_run'])
        resume_case = args.resume and run.exists()
        if resume_case:
            original = json.loads((run / 'run.json').read_text())
            if original['status'] == 'generated' and original.get('native_sop_finished_without_framework_error'):
                print(f'{case}: retained completed initial generation; no new generation calls', flush=True)
                return case, 0
        if args.serial:
            state['current_protocol'] = case
            state['cases'][case]['execution_stage'] = 'initial_generation'
            save(batch / 'batch.json', state)
        existing = state.get('in_memory_generation_workers', {}).get(case) if args.resume else None
        if existing:
            pid = existing['pid']
            stat = Path(f'/proc/{pid}/stat')
            fields = stat.read_text().split(') ', 1)[1].split()
            assert int(fields[19]) == existing['starttime_ticks'] and fields[0] == 'T'
            import signal
            os.kill(pid, signal.SIGCONT)
            print(f'{case}: resumed existing in-memory worker {pid}', flush=True)
            while True:
                try:
                    fields = stat.read_text().split(') ', 1)[1].split()
                except FileNotFoundError:
                    break
                if fields[0] == 'Z':
                    break
                time.sleep(5)
            result = json.loads((Path(state['cases'][case]['generation_run']) / 'run.json').read_text())
            return case, 0 if result['status'] == 'generated' and result.get('native_sop_finished_without_framework_error') else 1
        command = [str(PYTHON), str(BASE / 'fresh_round_experiment.py'), '--generation-worker',
                   '--case', case, '--out', str(batch)]
        if resume_case:
            command.append('--resume')
        log_path = (batch / 'logs' / f'{case}.generation_continued.log') if args.resume else batch / state['cases'][case]['process_log']
        with log_path.open('a' if args.resume else 'w') as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=BASE).returncode
        return case, code

    with concurrent.futures.ThreadPoolExecutor(max_workers=1 if args.serial else 4) as pool:
        results = map(launch, cases) if args.serial else pool.map(launch, cases)
        for case, code in results:
            original = json.loads((Path(state['cases'][case]['generation_run']) / 'run.json').read_text())
            state['cases'][case].update(generation_process_exit_code=code, generation_status=original['status'],
                                       generation_usage=original['usage'], source_hashes=original.get('project_hashes'))
            state['cases'][case]['execution_stage'] = 'initial_generation_completed' if code == 0 else 'initial_generation_failed'
            save(batch / 'batch.json', state)
            print(f'{case}: independent generation ended, exit {code}', flush=True)
    state['generation_wall_seconds'] = round(time.monotonic() - started, 3)
    failed = [case for case, item in state['cases'].items() if item['generation_process_exit_code'] != 0
              or item['generation_status'] != 'generated']
    if failed:
        state.update(status='generation_failed', failed_generation_cases=failed, generation_finished_at=now())
        save(batch / 'batch.json', state)
        print(f'Generation stopped with retained errors: {failed}', flush=True)
        return 1
    state.update(status='native_feedback', generation_finished_at=now())
    save(batch / 'batch.json', state)
    if args.serial:
        errors = []
        assessment_env = os.environ.copy()
        for key_name in {state['model']['key_env'], 'DS_API'}:
            assessment_env.pop(key_name, None)
        for case in cases:
            item = state['cases'][case]
            state.update(status='native_feedback', current_protocol=case)
            item.update(execution_stage='native_feedback', feedback_started_at=now())
            feedback_output = batch / 'feedback' / case
            source = item['generation_run']
            save(batch / 'batch.json', state)
            command = [str(PYTHON), str(BASE / 'feedback_experiment.py'), '--out', str(feedback_output),
                       '--jobs', '1', '--round', str(state['round']), '--sources', source]
            with (batch / 'logs' / f'{case}.native_feedback.log').open('w') as log:
                code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=BASE).returncode
            child = feedback_output / Path(source).name
            item.update(feedback_process_exit_code=code, feedback_output=str(feedback_output),
                        feedback_run=str(child), feedback_finished_at=now())
            if code != 0:
                item['execution_stage'] = 'native_feedback_failed'
                errors.append({'case': case, 'phase': 'native_feedback', 'exit_code': code})
                save(batch / 'batch.json', state)
                continue
            feedback_state = json.loads((child / 'run.json').read_text())
            item.update(feedback_status=feedback_state['status'], feedback_usage=feedback_state.get('usage'),
                        repair_rounds=feedback_state['repair_rounds'], self_tests_passed=feedback_state.get('passed'))
            if feedback_state['status'] != 'completed':
                item['execution_stage'] = 'native_feedback_failed'
                errors.append({'case': case, 'phase': 'native_feedback', 'reason': feedback_state.get('stop_reason')})
                save(batch / 'batch.json', state)
                continue
            if state.get('independent_assessment_entrypoint'):
                state.update(status='independent_assessment')
                item.update(execution_stage='independent_assessment', assessment_started_at=now())
                destination = batch / 'assessment' / case
                save(batch / 'batch.json', state)
                command = [str(PYTHON), str(batch / state['independent_assessment_entrypoint']),
                           '--feedback', str(feedback_output), '--destination', str(destination)]
                # Each feedback manifest contains one protocol, so the existing
                # assessor schedules one project and finishes before the next QA.
                with (batch / 'logs' / f'{case}.independent_assessment.log').open('w') as log:
                    code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                          cwd=BASE, env=assessment_env).returncode
                item.update(assessment_process_exit_code=code, assessment_output=str(destination),
                            assessment_finished_at=now(), assessment_llm_requests=0)
                if code != 0:
                    errors.append({'case': case, 'phase': 'independent_assessment', 'exit_code': code})
            item['execution_stage'] = 'completed' if code == 0 else 'independent_assessment_failed'
            save(batch / 'batch.json', state)
            print(f'{case}: serial native feedback and assessment finished, exit {code}', flush=True)
        state.update(status='completed' if not errors else 'completed_with_errors', serial_phase_errors=errors,
                     current_protocol=None, feedback_finished_at=now(), total_wall_seconds=round(time.monotonic() - started, 3))
        save(batch / 'batch.json', state)
        print(f"Serial round ended ({state['status']}): {batch}", flush=True)
        return 1 if errors else 0
    feedback_output = batch / 'feedback'
    command = [str(PYTHON), str(BASE / 'feedback_experiment.py'), '--out', str(feedback_output), '--jobs', '4',
               '--round', str(state['round']), '--sources', *[state['cases'][case]['generation_run'] for case in cases]]
    with (batch / 'logs/native_feedback.log').open('w') as log:
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=BASE).returncode
    state.update(status='feedback_completed' if code == 0 else 'feedback_failed', feedback_process_exit_code=code,
                 feedback_output=str(feedback_output), feedback_finished_at=now(),
                 generation_and_feedback_wall_seconds=round(time.monotonic() - started, 3))
    save(batch / 'batch.json', state)
    if code == 0 and state.get('independent_assessment_entrypoint'):
        state.update(status='independent_assessment', assessment_started_at=now())
        save(batch / 'batch.json', state)
        command = [str(PYTHON), str(batch / state['independent_assessment_entrypoint']),
                   '--feedback', str(feedback_output), '--destination', str(batch / 'assessment')]
        assessment_env = os.environ.copy()
        for key_name in {state['model']['key_env'], 'DS_API'}:
            assessment_env.pop(key_name, None)
        with (batch / 'logs/independent_assessment.log').open('w') as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                  cwd=BASE, env=assessment_env).returncode
        state.update(status='completed' if code == 0 else 'assessment_failed',
                     assessment_process_exit_code=code, assessment_finished_at=now(),
                     assessment_llm_requests=0, total_wall_seconds=round(time.monotonic() - started, 3))
        save(batch / 'batch.json', state)
    print(f"Independent round ended ({state['status']}): {batch}", flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
