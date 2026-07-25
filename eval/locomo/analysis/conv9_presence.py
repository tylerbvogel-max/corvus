"""Conv-9 corpus-presence test.

corvus_locomo holds conv 9's final graph. For every conv-9 question, measure
how much of the gold answer's content vocabulary exists ANYWHERE in the
active corpus. Separates 'the fact was never stored' (extraction failure)
from 'the fact was stored but the answer was still wrong/refused'
(retrieval or assembly failure).

Prints aggregates only. Read-only DB access.
"""
import collections
import json
import os
import re
import subprocess

import lib

SCRATCH = os.environ.get('SCRATCH', '/tmp')


def corpus():
    sql = ("select coalesce(json_agg(json_build_object('id',id,'a',is_active,"
           "'l',label,'c',coalesce(content,''))),'[]'::json) "
           "from neurons where node_type='lesson';")
    out = subprocess.run(['psql', '-d', 'corvus_locomo', '-t', '-A', '-c', sql],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main():
    neurons = corpus()
    active = [n for n in neurons if n['a']]
    print('conv9 corpus: %d lesson neurons, %d active' % (len(neurons), len(active)))
    vocab = set()
    for n in active:
        vocab |= lib.content_tokens(n['l'] + ' ' + n['c'])
    allvocab = set()
    for n in neurons:
        allvocab |= lib.content_tokens(n['l'] + ' ' + n['c'])
    print('active content-vocab size: %d (all incl. inactive: %d)' % (len(vocab), len(allvocab)))

    table, _ = lib.build()
    rows = [table[(9, i)] for i in range(204)]

    def coverage(gold):
        g = lib.content_tokens(gold)
        if not g:
            return None
        return len(g & vocab) / len(g)

    buckets = collections.defaultdict(list)
    for r in rows:
        if r['category'] == 5:
            continue
        cv = coverage(r['gold'])
        if cv is None:
            continue
        k = '1.00 (full)' if cv >= 0.999 else ('0.75-0.99' if cv >= 0.75 else
                                               ('0.50-0.74' if cv >= 0.5 else '<0.50'))
        buckets[k].append((r, cv))

    print('\n=== gold-answer vocabulary present in conv9 corpus vs outcome ===')
    print('gold-token coverage   n     mem_acc  refusal%  base_acc')
    for k in ['1.00 (full)', '0.75-0.99', '0.50-0.74', '<0.50']:
        v = buckets.get(k, [])
        if not v:
            continue
        print('%-20s %4d  %6.2f  %7.2f  %6.2f' % (
            k, len(v),
            lib.pct(sum(1 for r, _ in v if r['arms']['memory']['correct']), len(v)),
            lib.pct(sum(1 for r, _ in v if lib.is_refusal(r['arms']['memory']['pred'])), len(v)),
            lib.pct(sum(1 for r, _ in v if r['arms']['baseline']['correct']), len(v))))

    # The decisive split: refusals where the gold vocabulary IS fully present.
    nonadv = [r for r in rows if r['category'] != 5]
    ref = [r for r in nonadv if lib.is_refusal(r['arms']['memory']['pred'])]
    ref_covered = [r for r in ref if (coverage(r['gold']) or 0) >= 0.999]
    print('\n=== decisive split (conv9, non-adversarial) ===')
    print('non-adversarial questions:            %d' % len(nonadv))
    print('memory refused:                       %d (%.1f%%)' % (len(ref), lib.pct(len(ref), len(nonadv))))
    print('  ...of those, gold vocab FULLY in corpus: %d (%.1f%% of refusals)'
          % (len(ref_covered), lib.pct(len(ref_covered), len(ref))))
    print('  ...of those, baseline answered correctly: %d'
          % sum(1 for r in ref_covered if r['arms']['baseline']['correct']))

    wrong_sub = [r for r in nonadv if not r['arms']['memory']['correct']
                 and not lib.is_refusal(r['arms']['memory']['pred'])]
    ws_cov = [r for r in wrong_sub if (coverage(r['gold']) or 0) >= 0.999]
    print('memory answered but wrong:            %d' % len(wrong_sub))
    print('  ...gold vocab FULLY in corpus:      %d (%.1f%%)  -> assembly, not absence'
          % (len(ws_cov), lib.pct(len(ws_cov), len(wrong_sub))))

    print('\n=== same split, multi-hop only (category 1) ===')
    mh = [r for r in rows if r['category'] == 1]
    mhw = [r for r in mh if not r['arms']['memory']['correct']]
    mhw_cov = [r for r in mhw if (coverage(r['gold']) or 0) >= 0.999]
    print('multi-hop n=%d wrong=%d  gold-vocab-fully-present among wrong=%d (%.1f%%)'
          % (len(mh), len(mhw), len(mhw_cov), lib.pct(len(mhw_cov), len(mhw))))
    print('  of those wrong-with-facts-present, refusals=%d substantive=%d'
          % (sum(1 for r in mhw_cov if lib.is_refusal(r['arms']['memory']['pred'])),
             sum(1 for r in mhw_cov if not lib.is_refusal(r['arms']['memory']['pred']))))


if __name__ == '__main__':
    main()
