"""把 ECS 上的逐题结果瘦身为可下载的答案表。

原始单题 json 约 270 KB，其中绝大部分是 frames（SSE 原始帧），
本地评估只需要答案正文与元数据。本脚本只导出必要字段，
55 题合计约 200 KB，可直接下载到本地。
"""

import json
import sys
from pathlib import Path

ROOT = Path('/opt/rag-s8/state/executions/s88-lane')


def sources_of(answer: str) -> list[str]:
    """从结尾的 Sources 行提取文件名列表。"""
    for line in reversed(answer.splitlines()):
        text = line.strip()
        if text.startswith('Sources:'):
            return [part.strip() for part in text.split(':', 1)[1].split(',') if part.strip()]
    return []


def export(folder: Path) -> list[dict]:
    rows = []
    for path in sorted(folder.glob('*.json')):
        stem = path.stem
        # 跳过重试副本与汇总文件
        if stem.startswith('_') or '.a' in stem or 'partial' in stem:
            continue
        if not stem.isdigit():
            continue
        try:
            rec = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        answer = rec.get('answer') or ''
        rows.append({
            'ordinal': int(stem),
            'question': rec.get('question'),
            'answer': answer,
            'answer_len': len(answer),
            'elapsed': rec.get('elapsed_seconds'),
            'status': rec.get('status'),
            'has_sources': 'Sources:' in answer,
            'sources': sources_of(answer),
        })
    return rows


def main() -> int:
    folder = ROOT / (sys.argv[1] if len(sys.argv) > 1 else 'out-compact')
    rows = export(folder)
    target = folder / '_answers.jsonl'
    with target.open('w', encoding='utf-8', newline='\n') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    with_sources = sum(1 for r in rows if r['has_sources'])
    print(f'导出 {len(rows)} 题 -> {target}')
    print(f'含 Sources 行: {with_sources}/{len(rows)}')
    print(f'体积: {target.stat().st_size / 1024:.0f} KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
