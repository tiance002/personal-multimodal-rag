"""Offline passive memory diagnostic tests. No Docker, network, Provider or grant."""
import ast
import json
from pathlib import Path

import pytest

from scripts.s8_1_ecs_case_driver import MemorySampler, parse_meminfo, summarize_memory


def test_meminfo_kib_bytes_and_real_zero_swap():
    row = parse_meminfo('MemAvailable: 2048 kB\nSwapFree: 0 kB\n')
    assert row['status'] == 'MEASURED'
    assert row['MemAvailable_bytes'] == 2097152
    assert row['SwapFree_bytes'] == 0


@pytest.mark.parametrize('text', ['', 'MemAvailable: -1 kB\nSwapFree: nope kB\n',
    'MemAvailable: 12 MB\nSwapFree: 1 kB\n',
    'MemAvailable: 2 kB\nMemAvailable: 3 kB\nSwapFree: 1 kB\n'])
def test_missing_invalid_or_duplicate_available_never_becomes_zero(text):
    row = parse_meminfo(text)
    assert row['status'] == 'UNAVAILABLE'
    assert row['MemAvailable_bytes'] is None


def fake_process(tmp_path):
    proc = tmp_path / 'proc'; group = tmp_path / 'cgroup'
    (proc / '123').mkdir(parents=True)
    (group / 'controlled.scope').mkdir(parents=True)
    stat = '123 (python worker) ' + ' '.join(['S'] + ['0'] * 18 + ['98765'])
    (proc / '123' / 'stat').write_text(stat)
    (proc / '123' / 'cgroup').write_text('0::/controlled.scope\n')
    (proc / 'meminfo').write_text('MemAvailable: 1000 kB\nSwapFree: 0 kB\n')
    for name, value in [('memory.current','100'),('memory.peak','200'),('memory.max','1073741824')]:
        (group / 'controlled.scope' / name).write_text(value)
    return proc, group


def test_cgroup_current_peak_limit_and_pid_identity(tmp_path):
    proc, group = fake_process(tmp_path)
    sampler = MemorySampler(tmp_path, proc_root=proc, cgroup_root=group)
    sampler.bind_pid(123)
    row = sampler._container()
    assert row['status'] == 'MEASURED'
    assert (row['memory_current_bytes'],row['memory_peak_bytes'],row['memory_limit_bytes']) == (100,200,1073741824)
    stat = proc / '123' / 'stat'
    stat.write_text(stat.read_text().replace('98765','98766'))
    row = sampler._container()
    assert row['status'] == 'UNAVAILABLE' and row['memory_current_bytes'] is None


def test_cgroup_missing_peak_and_unlimited_limit_are_unavailable(tmp_path):
    proc, group = fake_process(tmp_path)
    (group / 'controlled.scope' / 'memory.peak').unlink()
    (group / 'controlled.scope' / 'memory.max').write_text('max')
    sampler = MemorySampler(tmp_path, proc_root=proc, cgroup_root=group)
    sampler.bind_pid(123)
    row = sampler._container()
    assert row['status'] == 'UNAVAILABLE'
    assert row['memory_current_bytes'] == 100
    assert row['memory_peak_bytes'] is row['memory_limit_bytes'] is None
    assert row['unavailable_fields']['memory_limit_bytes'] == 'UNBOUNDED'


def test_cgroup_traversal_or_missing_process_never_falls_back_to_host_root(tmp_path):
    proc, group = fake_process(tmp_path)
    (proc / '123' / 'cgroup').write_text('0::/../../other\n')
    sampler = MemorySampler(tmp_path, proc_root=proc, cgroup_root=group)
    sampler.bind_pid(123)
    assert sampler.binding is None and sampler._container()['status'] == 'UNAVAILABLE'
    sampler.bind_pid(999)
    assert sampler.binding is None and sampler._container()['memory_limit_bytes'] is None


def test_summary_uses_lowest_available_timestamp_and_valid_sample_count():
    rows = [{'at_utc':'2026-10-20T00:00:01+00:00','host':{'MemAvailable_bytes':2000,'SwapFree_bytes':0},'container':{'memory_current_bytes':100,'memory_peak_bytes':110,'memory_limit_bytes':1024}},
            {'at_utc':'2026-10-20T00:00:02+00:00','host':{'MemAvailable_bytes':None,'SwapFree_bytes':None},'container':{}},
            {'at_utc':'2026-10-20T00:00:03+00:00','host':{'MemAvailable_bytes':1000,'SwapFree_bytes':0},'container':{'memory_current_bytes':200,'memory_peak_bytes':250,'memory_limit_bytes':1024}}]
    summary = summarize_memory(rows)
    assert summary['sample_count'] == 3
    assert summary['host_MemAvailable_min'] == {'bytes':1000,'at_utc':'2026-10-20T00:00:03+00:00','valid_samples':2,'status':'MEASURED'}
    assert summary['container_memory_current_max']['bytes'] == 200
    assert summary['container_memory_peak_max']['bytes'] == 250
    assert summarize_memory([])['host_MemAvailable_min']['bytes'] is None


def test_thread_stop_saves_json_and_does_not_run_docker(tmp_path, monkeypatch):
    proc, group = fake_process(tmp_path)
    import scripts.s8_1_ecs_case_driver as driver
    def forbidden(*args, **kwargs): raise AssertionError('diagnostics must not start subprocess')
    monkeypatch.setattr(driver.subprocess,'run',forbidden)
    monkeypatch.setattr(driver.subprocess,'check_output',forbidden)
    sampler = MemorySampler(tmp_path, proc_root=proc, cgroup_root=group, interval=.01)
    sampler.start(); sampler.bind_pid(123)
    summary = sampler.stop()
    assert sampler.thread is not None and not sampler.thread.is_alive()
    assert summary['status'] == 'MEASURED' and summary['sample_count'] >= 2
    saved = json.loads((tmp_path / 'memory-samples.json').read_text())
    assert len(saved['samples']) == summary['sample_count']
    assert json.loads((tmp_path / 'memory-summary.json').read_text())['interval_seconds'] == .01


def test_missing_proc_and_diagnostic_write_error_do_not_escape_cleanup(tmp_path):
    sampler = MemorySampler(tmp_path, proc_root=tmp_path/'missing', cgroup_root=tmp_path/'missing-cgroup')
    sampler.sample_once()
    assert sampler.samples[-1]['host']['status'] == 'UNKNOWN'
    assert sampler.samples[-1]['host']['MemAvailable_bytes'] is None
    (tmp_path/'memory-samples.json').write_text('prior evidence must not be overwritten')
    summary = sampler.stop()
    assert summary['status'] == 'UNKNOWN'
    assert (tmp_path/'memory-samples.json').read_text() == 'prior evidence must not be overwritten'


def test_sampling_window_starts_before_container_and_stops_before_original_restore():
    import scripts.s8_1_ecs_case_driver as driver
    tree = ast.parse(Path(driver.__file__).read_text())
    run = next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='run')
    calls = [node for node in ast.walk(run) if isinstance(node,ast.Call)]
    start = next(node.lineno for node in calls if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='memory_sampler' and node.func.attr=='start')
    stop = next(node.lineno for node in calls if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='memory_sampler' and node.func.attr=='stop')
    def command_line(label):
        return next(node.lineno for node in calls if isinstance(node.func,ast.Name) and node.func.id=='execute_command' and len(node.args)>2 and isinstance(node.args[2],ast.Constant) and node.args[2].value==label)
    assert start < command_line('start-controlled-api') < command_line('normal-api-question-once') < command_line('stop-controlled-api') < stop < command_line('restore-original-api')