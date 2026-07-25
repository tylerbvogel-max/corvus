"""Join per-question evidence pointers to per-session distillation yield.

Tests whether wrongful refusals concentrate on sessions the distiller
compressed hardest. Prints aggregates only -- no question text, no gold text.
"""
import glob
import json
import os
import re
import collections
import statistics
import lib

ART = {
    0: '20260719T125853Z-p24461-conv0', 1: '20260719T143011Z-p6174-conv1',
    2: '20260719T183215Z-p15507-conv2', 3: '20260719T235657Z-p24338-conv3',
    4: '20260720T050225Z-p2124-conv4', 5: '20260720T102245Z-p29746-conv5',
    6: '20260720T152320Z-p28160-conv6', 7: '20260720T214354Z-p9380-conv7',
    8: '20260721T030558Z-p19090-conv8', 9: '20260721T075744Z-p1304-conv9',
}
DATASET = os.path.expanduser('~/Projects/corvus/eval/locomo/locomo10.json')


def session_yield(conv):
    """{session_int: (facts_saved, turns, facts_per_turn)}"""
    d = os.path.join(lib.ARTIFACTS, ART[conv] + '-full-lifecycle', 'episodes')
    saved = {}
    for f in glob.glob(d + '/*.distilled'):
        m = re.search(r'locomo-%d-(\d+)\.jsonl\.distilled$' % conv, f)
        if m:
            saved[int(m.group(1))] = json.load(open(f))['saved']
    return saved


def evidence_sessions(ev):
    out = set()
    for e in ev or []:
        m = re.match(r'D(\d+):', str(e))
        if m:
            out.add(int(m.group(1)))
    return out


def build():
    data = json.load(open(DATASET))
    table, _ = lib.build()
    recs = []
    for conv in range(10):
        c = data[conv]
        saved = session_yield(conv)
        turns = {int(k.split('_')[1]): len(c['conversation'][k])
                 for k in c['conversation'] if re.fullmatch(r'session_\d+', k)}
        qa = c['qa']
        for i, q in enumerate(qa):
            row = table[(conv, i)]
            assert row['question'].strip() == q['question'].strip(), (conv, i)
            es = evidence_sessions(q.get('evidence'))
            ys = [saved.get(s, 0) / turns[s] for s in es if s in turns]
            recs.append({
                'conv': conv, 'qidx': i, 'category': q['category'],
                'n_evidence': len(q.get('evidence') or []),
                'n_ev_sessions': len(es),
                'ev_sessions': sorted(es),
                'min_yield': min(ys) if ys else None,
                'mean_yield': statistics.mean(ys) if ys else None,
                'ev_saved': sum(saved.get(s, 0) for s in es),
                'row': row,
            })
    return recs, {c: session_yield(c) for c in range(10)}


if __name__ == '__main__':
    recs, _ = build()
    na = [r for r in recs if r['category'] != 5 and r['min_yield'] is not None]
    print('non-adversarial questions with usable evidence pointers:', len(na))

    print('\n=== distillation yield of evidence sessions vs outcome (memory arm) ===')
    def bucket(y):
        for t in (0.3, 0.45, 0.6, 0.8):
            if y < t:
                return '<%.2f' % t
        return '>=0.80'
    b = collections.defaultdict(list)
    for r in na:
        b[bucket(r['min_yield'])].append(r)
    order = ['<0.30', '<0.45', '<0.60', '<0.80', '>=0.80']
    print('min facts/turn   n     acc   refusal%  base_acc')
    for k in order:
        v = b.get(k, [])
        if not v:
            continue
        acc = lib.pct(sum(1 for r in v if r['row']['arms']['memory']['correct']), len(v))
        ref = lib.pct(sum(1 for r in v if lib.is_refusal(r['row']['arms']['memory']['pred'])), len(v))
        bac = lib.pct(sum(1 for r in v if r['row']['arms']['baseline']['correct']), len(v))
        print('%-14s %4d  %6.2f  %7.2f  %6.2f' % (k, len(v), acc, ref, bac))

    print('\n=== number of evidence turns required vs outcome (proxy for hop count) ===')
    b2 = collections.defaultdict(list)
    for r in na:
        b2[min(r['n_evidence'], 4)].append(r)
    print('n_evid   n     mem_acc  ref%   base_acc')
    for k in sorted(b2):
        v = b2[k]
        print('%-8s %4d  %6.2f  %6.2f  %6.2f' % (
            k if k < 4 else '4+', len(v),
            lib.pct(sum(1 for r in v if r['row']['arms']['memory']['correct']), len(v)),
            lib.pct(sum(1 for r in v if lib.is_refusal(r['row']['arms']['memory']['pred'])), len(v)),
            lib.pct(sum(1 for r in v if r['row']['arms']['baseline']['correct']), len(v))))

    print('\n=== evidence spread: single-session vs cross-session evidence ===')
    for label, sub in [('1 session', [r for r in na if r['n_ev_sessions'] == 1]),
                       ('2+ sessions', [r for r in na if r['n_ev_sessions'] >= 2])]:
        print('%-12s n=%4d mem=%6.2f ref%%=%6.2f base=%6.2f' % (
            label, len(sub),
            lib.pct(sum(1 for r in sub if r['row']['arms']['memory']['correct']), len(sub)),
            lib.pct(sum(1 for r in sub if lib.is_refusal(r['row']['arms']['memory']['pred'])), len(sub)),
            lib.pct(sum(1 for r in sub if r['row']['arms']['baseline']['correct']), len(sub))))

    print('\n=== per-conv distillation compression ===')
    print('conv  sessions  turns  facts  facts/turn  nonadv_ref%  score')
    data = json.load(open(DATASET))
    table, _ = lib.build()
    for conv in range(10):
        c = data[conv]
        saved = session_yield(conv)
        turns = sum(len(c['conversation'][k]) for k in c['conversation']
                    if re.fullmatch(r'session_\d+', k))
        rs = [table[(conv, i)] for i in range(len(c['qa']))]
        nonadv = [r for r in rs if r['category'] != 5]
        print('%d %8d %7d %6d %10.3f %11.1f %6.2f' % (
            conv, len(saved), turns, sum(saved.values()), sum(saved.values()) / turns,
            lib.pct(sum(1 for r in nonadv if lib.is_refusal(r['arms']['memory']['pred'])), len(nonadv)),
            lib.pct(sum(1 for r in rs if r['arms']['memory']['correct']), len(rs))))
