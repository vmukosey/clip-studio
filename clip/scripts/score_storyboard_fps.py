"""Blindly grade completed storyboard FPS summaries against manual references."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storyboard_fps_experiment import MODES, OUTPUT, SCENES, ask


REFERENCE = OUTPUT.parent / 'storyboard-fps-reference.json'
RESULT = OUTPUT.parent / 'storyboard-fps-scores.json'


def main():
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))['scenes']
    scores = json.loads(RESULT.read_text(encoding='utf-8')) if RESULT.exists() else {}
    for scene in SCENES:
        if scene in scores:
            continue
        path = OUTPUT.with_name(f'storyboard-fps-{scene}.json')
        if not path.exists():
            raise RuntimeError(f'Missing {scene} results')
        results = json.loads(path.read_text(encoding='utf-8'))['scenes'][scene]['modes']
        if any(mode not in results or 'summary' not in results[mode] for mode in MODES):
            raise RuntimeError(f'Incomplete {scene} results')
        modes = list(MODES)
        random.Random(scene).shuffle(modes)
        aliases = {chr(65 + i): mode for i, mode in enumerate(modes)}
        candidates = {alias: results[mode]['summary']['answer'] for alias, mode in aliases.items()}
        prompt = (
            'Grade six anonymized video chronologies against the independent reference. '
            'Judge only what is in each final chronology and hooks. Do not infer from the reference '
            'that a candidate detected an event. For each candidate return JSON keyed by alias '
            'with detection, order, boundaries, factuality and hook scores (integers 0-5), '
            'required_events object mapping each named requirement to true/false, and false_claims '
            'array listing unsupported assertions. Detection means visible key actions, order means '
            'chronology, boundaries means timing within approximately 1-2 seconds, factuality '
            'means no visual claim inferred only from speech, hook means short and supported. '
            'A false claim requires factuality below 5. Keep speech distinct from vision. '
            f'Reference: {json.dumps(reference[scene], ensure_ascii=False)}. '
            f'Candidates: {json.dumps(candidates, ensure_ascii=False)}'
        )
        answer = ask(prompt, [])
        if set(answer['answer']) != set(aliases):
            raise RuntimeError(f'Invalid score aliases for {scene}')
        scores[scene] = {'alias_to_mode': aliases, 'grades': answer['answer'],
                         'judge_seconds': answer['seconds'], 'judge_usage': answer['usage']}
        RESULT.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding='utf-8')
        print(scene, 'scored', flush=True)
    summary = {}
    for mode in MODES:
        values = []
        required = True
        false_claims = []
        seconds = 0
        usage = []
        for scene in SCENES:
            aliases = scores[scene]['alias_to_mode']
            alias = next(alias for alias, candidate_mode in aliases.items() if candidate_mode == mode)
            grade = scores[scene]['grades'][alias]
            values.append(sum(grade[field] for field in
                              ('detection', 'order', 'boundaries', 'factuality', 'hook')))
            required &= all(grade.get('required_events', {}).values())
            false_claims.extend(f'{scene}: {claim}' for claim in grade.get('false_claims', []))
            data = json.loads(OUTPUT.with_name(f'storyboard-fps-{scene}.json').read_text(encoding='utf-8'))
            run = data['scenes'][scene]['modes'][mode]
            for block in run['blocks'] + [run['summary']]:
                seconds += block['seconds']
                usage.append(block['usage'])
        summary[mode] = {'score': sum(values), 'max_score': 25 * len(SCENES),
                         'required_events': required, 'false_claims': false_claims,
                         'response_seconds': round(seconds, 2), 'usage': usage}
    best = max(item['score'] for item in summary.values())
    for mode, item in summary.items():
        item['within_5_percent'] = item['score'] >= .95 * best
        item['eligible'] = item['required_events'] and not item['false_claims'] and item['within_5_percent']
    summary_path = OUTPUT.parent / 'storyboard-fps-summary.json'
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: {field: v[field] for field in ('score', 'eligible', 'response_seconds')}
                      for k, v in summary.items()}, indent=2))


if __name__ == '__main__':
    main()
