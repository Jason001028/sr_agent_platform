"""Live-model paired evaluation; uses existing SR_LLM configuration, never mock scores.
Run: python -m backend.eval_viewer_context --output viewer-eval.json
"""
import argparse
import json
from backend.agent.loop import run_loop
from backend.config import load_config
from backend.services.viewer_context import validate_snapshot

CASES = [
    ('metadata', '请给出本图像信息', 'get_current_image_info'),
    ('dimensions', '当前影像的宽高是多少？', 'get_current_image_info'),
    ('visual_without_pixels', '本图有没有明显条带？', None),
    ('search', '请搜索场景库中 GF07 的影像', 'search_scenes'),
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='viewer-eval.json')
    args = parser.parse_args()
    cfg = load_config()
    if cfg.llm_mock or not cfg.llm_base_url:
        raise SystemExit('Requires real SR_LLM endpoint; mock metrics are not model evaluation.')
    rows = []
    for enabled in (False, True):
        for case, prompt, expected in CASES:
            events = []
            snapshot = validate_snapshot({'version': 1, 'current': {'id': 1, 'name': 'evaluation_A.tif',
                'width': 100, 'height': 200, 'metadata_source': 'evaluation_fixture'}, 'displayed': []})
            result = run_loop(cfg, prompt, viewer_snapshot=snapshot if enabled else None,
                              on_step=events.append, max_tool_calls=6)
            calls = [e['name'] for e in events if e.get('type') == 'tool_call']
            rows.append({'enhanced': enabled, 'case': case, 'expected_first': expected,
                'first_tool_correct': (calls[0] if calls else None) == expected,
                'irrelevant_searches': calls.count('search_scenes') if case != 'search' else 0,
                'calls': calls, 'answer': result.get('answer'), 'ok': result['ok'], 'error': result.get('error'),
                'answer_accuracy': None, 'review_note': 'Human review required: dimensions/name, visual abstention, search existence claims'})
    with open(args.output, 'w', encoding='utf-8') as out:
        json.dump({'rows': rows, 'note': 'Baseline ablates observation only; shared new tool/policy. Not historical model reproduction.'}, out, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    main()
