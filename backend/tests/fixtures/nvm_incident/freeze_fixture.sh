#!/usr/bin/env bash
# Freeze the NVM duplicate-family incident (mind-reconsolidation-kernel golden specimen).
# Immutable snapshot of live corvus_mind state for members {47,51,57,177,1151,1161}
# plus older absorbed lineage {71,85,109,116,126,128,156,172,209,211} and
# proposals {1067,1069,1071,1099}. Re-running OVERWRITES; the committed copy +
# MANIFEST.sha256 is the frozen artifact — do not re-run against live after freeze.
set -euo pipefail
cd "$(dirname "$0")"
DB="corvus_mind"; U="yggdrasil"
IDS="47,51,57,177,1151,1161,71,85,109,116,126,128,156,172,209,211"
CORE_IDS="47,51,57,177,1151,1161"
PROPS="1067,1069,1071,1099"

dump() { # name, sql, [order-expr]
  local ord="${3:-t.id}"
  psql -U "$U" -d "$DB" -t -A -c "SELECT COALESCE(json_agg(t ORDER BY $ord), '[]'::json) FROM ($2) t" > "$1.json"
  python3 -m json.tool "$1.json" > "$1.tmp" && mv "$1.tmp" "$1.json"
  echo "  $1.json: $(python3 -c "import json;print(len(json.load(open('$1.json'))))") rows"
}

dump neurons              "SELECT * FROM neurons WHERE id IN ($IDS)"
dump neuron_edges         "SELECT * FROM neuron_edges WHERE source_id IN ($IDS) OR target_id IN ($IDS)" "t.source_id, t.target_id, t.edge_type"
dump neuron_firings       "SELECT * FROM neuron_firings WHERE neuron_id IN ($IDS)"
dump synaptic_learning_events "SELECT * FROM synaptic_learning_events WHERE neuron_id IN ($IDS)"
dump memory_change_log    "SELECT * FROM memory_change_log WHERE neuron_id IN ($IDS)"
dump neuron_refinements   "SELECT * FROM neuron_refinements WHERE neuron_id IN ($IDS)"
dump mind_pair_verdicts   "SELECT * FROM mind_pair_verdicts WHERE neuron_a_id IN ($IDS) OR neuron_b_id IN ($IDS)"
dump autopilot_proposals  "SELECT * FROM autopilot_proposals WHERE id IN ($PROPS)"
dump proposal_items       "SELECT * FROM proposal_items WHERE proposal_id IN ($PROPS) OR target_neuron_id IN ($IDS) OR created_neuron_id IN ($IDS)"
dump actions              "SELECT * FROM actions WHERE source_proposal_id IN ($PROPS) OR parent_action_id IN (SELECT id FROM actions WHERE source_proposal_id IN ($PROPS))"
dump neuron_source_links  "SELECT * FROM neuron_source_links WHERE neuron_id IN ($IDS)"
dump neuron_score_overrides "SELECT * FROM neuron_score_overrides WHERE neuron_id IN ($IDS)"
# Trimmed query projection: enough to identify/replay the query set without prompt bloat.
dump queries              "SELECT id, left(user_message, 300) AS user_message, classified_intent, created_at FROM queries WHERE id IN (SELECT DISTINCT query_id FROM neuron_firings WHERE neuron_id IN ($IDS) AND query_id IS NOT NULL)"

# Headline invariants, frozen at snapshot time (core 6-member set).
psql -U "$U" -d "$DB" -t -A -c "
SELECT json_build_object(
  'frozen_at', now(),
  'core_member_ids', ARRAY[$CORE_IDS],
  'all_member_ids', ARRAY[$IDS],
  'proposal_ids', ARRAY[$PROPS],
  'summed_firing_rows', (SELECT count(*) FROM neuron_firings WHERE neuron_id IN ($CORE_IDS)),
  'max_member_distinct_queries', (SELECT max(c) FROM (SELECT count(DISTINCT query_id) c FROM neuron_firings WHERE neuron_id IN ($CORE_IDS) GROUP BY neuron_id) m),
  'union_distinct_queries', (SELECT count(DISTINCT query_id) FROM neuron_firings WHERE neuron_id IN ($CORE_IDS)),
  'internal_activation_edges', (SELECT count(*) FROM neuron_edges WHERE source_id IN ($CORE_IDS) AND target_id IN ($CORE_IDS) AND edge_type IN ('pyramidal','stellate')),
  'internal_evidence_links', (SELECT count(*) FROM neuron_edges WHERE source_id IN ($CORE_IDS) AND target_id IN ($CORE_IDS) AND edge_type = 'evidence-link'),
  'active_members', (SELECT json_agg(id ORDER BY id) FROM neurons WHERE id IN ($CORE_IDS) AND is_active),
  'approved_unapplied_proposals', (SELECT json_agg(id ORDER BY id) FROM autopilot_proposals WHERE id IN ($PROPS) AND state='approved' AND applied_at IS NULL),
  'neuron_content_sha256', (SELECT json_object_agg(id, encode(sha256(content::bytea),'hex')) FROM neurons WHERE id IN ($IDS)),
  'neuron_embedding_sha256', (SELECT json_object_agg(id, encode(sha256(embedding::text::bytea),'hex')) FROM neurons WHERE id IN ($IDS) AND embedding IS NOT NULL)
)" | python3 -m json.tool > invariants.json
echo "  invariants.json written"

sha256sum *.json > MANIFEST.sha256
echo "Frozen. Manifest:"
cat MANIFEST.sha256
